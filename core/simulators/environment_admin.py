# Copyright (c) 2024 KubeEdge Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import json
import logging
import psutil
import docker
import yaml
import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from .exceptions import (
    ConfigurationError, HostValidationError, DockerError
)
from .security import generate_secure_password

logger = logging.getLogger(__name__)


class HostValidator:
    """Validates host system meets requirements"""
    
    MIN_MEMORY_GB = 4
    MIN_DISK_GB = 10
    
    def __init__(self):
        self.docker_client = None
        self.results = {}
    
    def validate_all(self) -> Dict[str, bool]:
        """Run all validations"""
        self.results = {
            'memory': self._validate_memory(),
            'disk': self._validate_disk(),
            'docker': self._validate_docker(),
            'python': self._validate_python(),
        }
        
        for check_name, result in self.results.items():
            status = 'PASS' if result else 'FAIL'
            logger.info(f'Host validation [{check_name}]: {status}')
        
        if not all(self.results.values()):
            failed = [k for k, v in self.results.items() if not v]
            msg = f'Host validation failed: {", ".join(failed)}'
            raise HostValidationError(msg)
        
        return self.results
    
    def _validate_memory(self) -> bool:
        """Check available memory"""
        try:
            available_gb = psutil.virtual_memory().available / (1024 ** 3)
            if available_gb < self.MIN_MEMORY_GB:
                logger.error(
                    f'Insufficient memory: {available_gb:.1f}GB < {self.MIN_MEMORY_GB}GB'
                )
                return False
            logger.debug(f'Memory OK: {available_gb:.1f}GB')
            return True
        except Exception as e:
            logger.error(f'Memory check error: {e}')
            return False
    
    def _validate_disk(self) -> bool:
        """Check available disk space"""
        try:
            disk = psutil.disk_usage('/')
            available_gb = disk.free / (1024 ** 3)
            if available_gb < self.MIN_DISK_GB:
                logger.error(
                    f'Insufficient disk: {available_gb:.1f}GB < {self.MIN_DISK_GB}GB'
                )
                return False
            logger.debug(f'Disk OK: {available_gb:.1f}GB')
            return True
        except Exception as e:
            logger.error(f'Disk check error: {e}')
            return False
    
    def _validate_docker(self) -> bool:
        """Check Docker availability"""
        try:
            self.docker_client = docker.from_env()
            self.docker_client.ping()
            logger.debug('Docker connection OK')
            return True
        except Exception as e:
            logger.error(f'Docker validation error: {e}')
            return False
    
    def _validate_python(self) -> bool:
        """Check Python version"""
        try:
            import sys
            if sys.version_info.major == 3 and sys.version_info.minor >= 8:
                logger.debug(f'Python version OK: {sys.version}')
                return True
            logger.error(f'Python version too old: {sys.version}')
            return False
        except Exception as e:
            logger.error(f'Python check error: {e}')
            return False


class EnvironmentAdministrator:
    """Manages simulation environment lifecycle"""
    
    def __init__(self, config_path: str):
        """Initialize environment administrator"""
        if not config_path or not isinstance(config_path, str):
            raise ValueError('Invalid config path')
        
        self.config_path = config_path
        self.config = None
        self.env_id = None
        self.env_dir = None
        self.docker_client = None
        
        self._validate_config_path()
    
    def _validate_config_path(self) -> None:
        """Validate configuration file exists"""
        path = Path(self.config_path)
        
        if not path.exists():
            raise ConfigurationError(f'Config not found: {self.config_path}')
        
        if not path.is_file():
            raise ConfigurationError(f'Config is not a file: {self.config_path}')
        
        if not os.access(path, os.R_OK):
            raise ConfigurationError(f'Config not readable: {self.config_path}')
    
    def load_config(self) -> Dict[str, Any]:
        """Load YAML configuration"""
        try:
            with open(self.config_path, 'r') as f:
                self.config = yaml.safe_load(f)
            
            if not self.config:
                raise ConfigurationError('Config file is empty')
            
            self._validate_config_structure()
            logger.info(f'Config loaded: {self.config_path}')
            return self.config
        
        except yaml.YAMLError as e:
            raise ConfigurationError(f'Invalid YAML: {e}')
        except Exception as e:
            raise ConfigurationError(f'Config load error: {e}')
    
    def _validate_config_structure(self) -> None:
        """Validate required config fields"""
        required = ['environment', 'host_requirements']
        for field in required:
            if field not in self.config:
                raise ConfigurationError(f'Missing field: {field}')
    
    def check_host(self) -> Dict[str, bool]:
        """Validate host meets requirements and store the Docker client."""
        validator = HostValidator()
        validator.validate_all()
        self.docker_client = validator.docker_client
        return validator.results
    
    def build_environment(self) -> str:
        """Build simulation environment"""
        if not self.config:
            self.load_config()
        
        self.check_host()
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        random_id = secrets.token_hex(4)
        self.env_id = f'sim_{timestamp}_{random_id}'
        
        self.env_dir = f'/tmp/ianvs_sim/{self.env_id}'
        os.makedirs(self.env_dir, mode=0o700, exist_ok=True)
        
        subdirs = ['data', 'models', 'logs', 'configs', 'docker', 'secrets']
        for subdir in subdirs:
            subdir_path = os.path.join(self.env_dir, subdir)
            os.makedirs(subdir_path, mode=0o700, exist_ok=True)
        
        self._save_metadata()
        self._create_network()
        
        logger.info(f'Environment built: {self.env_id}')
        return self.env_id
    
    def _save_metadata(self) -> None:
        """Save environment metadata"""
        metadata = {
            'environment_id': self.env_id,
            'created_at': datetime.now().isoformat(),
            'environment_dir': self.env_dir,
        }
        
        meta_file = os.path.join(self.env_dir, 'metadata.json')
        with open(meta_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        os.chmod(meta_file, 0o600)
    
    def _create_network(self) -> None:
        """Create Docker network"""
        if not self.docker_client:
            return
        
        network_name = f'{self.env_id}_net'
        
        try:
            self.docker_client.networks.create(
                network_name,
                driver='bridge',
                check_duplicate=True
            )
            logger.info(f'Network created: {network_name}')
        except docker.errors.APIError as e:
            logger.warning(f'Network creation warning: {e}')
    
    def deploy_modules(self, modules: List[str]) -> Dict[str, bool]:
        """Deploy infrastructure modules"""
        if not self.env_id:
            raise EnvironmentError('Environment not built')
        
        results = {}
        for module in modules:
            try:
                self._deploy_module(module)
                results[module] = True
                logger.info(f'Module deployed: {module}')
            except Exception as e:
                results[module] = False
                logger.error(f'Module deployment failed [{module}]: {e}')
        
        results_file = os.path.join(self.env_dir, 'deploy_results.json')
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        os.chmod(results_file, 0o600)
        
        return results
    
    def _deploy_module(self, module_name: str) -> None:
        """Deploy single module"""
        module_config = self.config.get('modules', {}).get(module_name)
        
        if not module_config:
            raise ValueError(f'Module not configured: {module_name}')
        
        if module_name == 'database':
            self._deploy_database(module_config)
        elif module_name == 'message_queue':
            self._deploy_message_queue(module_config)
        elif module_name == 'monitoring':
            self._deploy_monitoring(module_config)
    
    def _deploy_database(self, config: Dict) -> None:
        """Deploy PostgreSQL database"""
        if not self.docker_client:
            raise DockerError('Docker not available')
        
        container_name = f'{self.env_id}_db'
        db_password = generate_secure_password()
        db_user = config.get('user', 'ianvs_user')
        db_name = config.get('database', 'ianvs_sim')
        
        try:
            self.docker_client.containers.run(
                image=config.get('image', 'postgres:15'),
                name=container_name,
                environment={
                    'POSTGRES_USER': db_user,
                    'POSTGRES_PASSWORD': db_password,
                    'POSTGRES_DB': db_name,
                },
                volumes=[
                    f'{self.env_dir}/data:/var/lib/postgresql/data',
                    f'{self.env_dir}/logs:/var/log/postgresql'
                ],
                network=f'{self.env_id}_net',
                restart_policy={'Name': 'unless-stopped'},
                detach=True,
                mem_limit='1g'
            )
            
            creds = {
                'host': container_name,
                'user': db_user,
                'password': db_password,
                'database': db_name,
                'port': 5432
            }
            
            creds_file = os.path.join(self.env_dir, 'secrets', 'db.json')
            with open(creds_file, 'w') as f:
                json.dump(creds, f)
            os.chmod(creds_file, 0o600)
            
            logger.info(f'Database deployed: {container_name}')
        
        except docker.errors.APIError as e:
            raise DockerError(f'Database deployment failed: {e}')
    
    def _deploy_message_queue(self, config: Dict) -> None:
        """Deploy Redis message queue"""
        if not self.docker_client:
            raise DockerError('Docker not available')
        
        container_name = f'{self.env_id}_mq'
        mq_password = generate_secure_password()
        
        try:
            self.docker_client.containers.run(
                image=config.get('image', 'redis:7-alpine'),
                name=container_name,
                command=f'redis-server --requirepass {mq_password}',
                network=f'{self.env_id}_net',
                restart_policy={'Name': 'unless-stopped'},
                detach=True,
                mem_limit='512m'
            )
            
            creds = {
                'host': container_name,
                'port': 6379,
                'password': mq_password,
                'db': 0
            }
            
            creds_file = os.path.join(self.env_dir, 'secrets', 'redis.json')
            with open(creds_file, 'w') as f:
                json.dump(creds, f)
            os.chmod(creds_file, 0o600)
            
            logger.info(f'Message queue deployed: {container_name}')
        
        except docker.errors.APIError as e:
            raise DockerError(f'Message queue deployment failed: {e}')
    
    def _deploy_monitoring(self, config: Dict) -> None:
        """Deploy monitoring stack"""
        if not self.docker_client:
            raise DockerError('Docker not available')
        
        container_name = f'{self.env_id}_mon'
        
        try:
            self.docker_client.containers.run(
                image=config.get('image', 'prom/prometheus:latest'),
                name=container_name,
                volumes=[f'{self.env_dir}/logs:/prometheus'],
                network=f'{self.env_id}_net',
                restart_policy={'Name': 'unless-stopped'},
                detach=True,
                mem_limit='256m'
            )
            
            logger.info(f'Monitoring deployed: {container_name}')
        
        except docker.errors.APIError as e:
            raise DockerError(f'Monitoring deployment failed: {e}')
    
    def cleanup_environment(self) -> bool:
        """Cleanup environment"""
        if not self.env_id:
            logger.warning('No environment to cleanup')
            return True
        
        try:
            self._stop_containers()
            self._remove_network()
            self._cleanup_files()
            logger.info(f'Environment cleaned: {self.env_id}')
            return True
        except Exception as e:
            logger.error(f'Cleanup error: {e}')
            return False
    
    def _stop_containers(self) -> None:
        """Stop containers"""
        if not self.docker_client:
            return
        
        try:
            for cont in self.docker_client.containers.list():
                if self.env_id in cont.name:
                    cont.stop(timeout=10)
                    cont.remove()
                    logger.info(f'Container removed: {cont.name}')
        except docker.errors.APIError as e:
            logger.warning(f'Container cleanup: {e}')
    
    def _remove_network(self) -> None:
        """Remove network"""
        if not self.docker_client:
            return
        
        network_name = f'{self.env_id}_net'
        try:
            net = self.docker_client.networks.get(network_name)
            net.remove()
            logger.info(f'Network removed: {network_name}')
        except (docker.errors.NotFound, docker.errors.APIError) as e:
            logger.warning(f'Network cleanup: {e}')
    
    def _cleanup_files(self) -> None:
        """Cleanup files"""
        import shutil
        try:
            if self.env_dir and os.path.exists(self.env_dir):
                shutil.rmtree(self.env_dir)
                logger.info(f'Files removed: {self.env_dir}')
        except Exception as e:
            logger.warning(f'File cleanup: {e}')
    
    def get_status(self) -> Dict[str, Any]:
        """Get environment status"""
        if not self.env_id:
            return {'status': 'not_initialized'}
        
        return {
            'environment_id': self.env_id,
            'environment_dir': self.env_dir,
            'containers': self._get_containers(),
            'resources': self._get_resources()
        }
    
    def _get_containers(self) -> List[Dict]:
        """Get container list"""
        if not self.docker_client:
            return []
        
        containers = []
        try:
            for cont in self.docker_client.containers.list():
                if self.env_id in cont.name:
                    containers.append({
                        'name': cont.name,
                        'status': cont.status,
                        'image': cont.image.tags[0] if cont.image.tags else 'unknown'
                    })
        except docker.errors.APIError as e:
            logger.warning(f'Container status error: {e}')
        
        return containers
    
    def _get_resources(self) -> Dict[str, float]:
        """Get system resources"""
        return {
            'memory_gb': round(psutil.virtual_memory().available / (1024 ** 3), 2),
            'cpu_percent': psutil.cpu_percent(interval=1),
            'disk_gb': round(psutil.disk_usage('/').free / (1024 ** 3), 2)
        }