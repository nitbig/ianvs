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
import yaml
import docker
import redis
import secrets
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable
import threading
import time
import uuid

from .exceptions import (
    JobConfigError, JobDeploymentError, RedisConnectionError
)
from .security import generate_secure_password, generate_token

logger = logging.getLogger(__name__)


class RedisManager:
    """Manages Redis connections securely"""
    
    def __init__(self, host: str, port: int, password: str, db: int = 0):
        self.host = host
        self.port = port
        self.password = password
        self.db = db
        self.connection = None
        self._connect()
    
    def _connect(self) -> None:
        """Establish Redis connection"""
        try:
            self.connection = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                db=self.db,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True
            )
            self.connection.ping()
            logger.info(f'Redis connected: {self.host}:{self.port}')
        except redis.ConnectionError as e:
            raise RedisConnectionError(f'Redis connection failed: {e}')
    
    def get(self) -> redis.Redis:
        """Get connection"""
        return self.connection
    
    def close(self) -> None:
        """Close connection"""
        if self.connection:
            self.connection.close()


class JobValidator:
    """Validates job configuration"""
    
    REQUIRED = {'name', 'algorithm', 'workers', 'dataset'}
    
    @classmethod
    def validate(cls, config: Dict) -> bool:
        """Validate job config"""
        if not isinstance(config, dict):
            raise JobConfigError('Config must be dictionary')
        
        missing = cls.REQUIRED - set(config.keys())
        if missing:
            raise JobConfigError(f'Missing fields: {missing}')
        
        if not isinstance(config['workers'], int) or config['workers'] < 1:
            raise JobConfigError('Workers must be positive integer')
        
        if not isinstance(config['name'], str) or not config['name'].strip():
            raise JobConfigError('Name must be non-empty string')
        
        return True


class JobAdministrator:
    """Manages simulation jobs"""
    
    def __init__(self, env_id: str, env_dir: str):
        self.env_id = env_id
        self.env_dir = env_dir
        self.docker_client = docker.from_env()
        self.redis_mgr = None
        self.jobs = {}
        self.monitors = {}
    
    def build_image(self, algo_name: str, dockerfile_path: str,
                   tag: str = None) -> str:
        """Build algorithm Docker image"""
        
        if not algo_name or not isinstance(algo_name, str):
            raise ValueError('Invalid algorithm name')
        
        if not os.path.exists(dockerfile_path):
            raise FileNotFoundError(f'Dockerfile not found: {dockerfile_path}')
        
        if tag is None:
            tag = f'ianvs/{algo_name}:latest'
        
        dockerfile_dir = os.path.dirname(dockerfile_path)
        
        try:
            logger.info(f'Building image: {tag}')
            image, logs = self.docker_client.images.build(
                path=dockerfile_dir,
                dockerfile=os.path.basename(dockerfile_path),
                tag=tag,
                rm=True
            )
            
            for log_entry in logs:
                if 'stream' in log_entry:
                    logger.debug(log_entry['stream'].strip())
            
            logger.info(f'Image built: {tag}')
            return tag
        
        except docker.errors.APIError as e:
            raise JobDeploymentError(f'Image build failed: {e}')
    
    def generate_job_yaml(self, job_config: Dict, output_path: str) -> str:
        """Generate job YAML specification"""
        
        JobValidator.validate(job_config)
        
        spec = {
            'apiVersion': 'ianvs/v1',
            'kind': 'SimulationJob',
            'metadata': {
                'name': job_config['name'],
                'namespace': 'ianvs-sim',
                'labels': {
                    'environment': self.env_id,
                    'created_at': datetime.now().isoformat()
                }
            },
            'spec': {
                'algorithm': {
                    'image': job_config.get('algo_image', 
                                          f"ianvs/{job_config['algorithm']}:latest"),
                    'name': job_config['algorithm'],
                    'params': job_config.get('algo_params', {})
                },
                'workers': self._gen_workers(job_config),
                'dataset': {
                    'path': job_config['dataset'],
                    'format': job_config.get('dataset_format', 'image'),
                    'split': job_config.get('split', {'train': 0.8, 'test': 0.2})
                },
                'execution': {
                    'epochs': job_config.get('epochs', 10),
                    'batch_size': job_config.get('batch_size', 32),
                    'learning_rate': job_config.get('learning_rate', 0.001)
                },
                'resources': {
                    'memory': job_config.get('memory_limit', '2Gi'),
                    'cpu': job_config.get('cpu_limit', '1'),
                    'gpu': job_config.get('gpu_required', False)
                },
                'monitoring': {
                    'metrics': job_config.get('metrics', ['accuracy', 'loss']),
                    'interval': job_config.get('monitoring_interval', 5)
                }
            }
        }
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            yaml.dump(spec, f, default_flow_style=False)
        
        os.chmod(output_path, 0o600)
        logger.info(f'Job YAML generated: {output_path}')
        
        return output_path
    
    def _gen_workers(self, config: Dict) -> List[Dict]:
        """Generate worker specs"""
        num = config['workers']
        workers = []
        
        for i in range(num):
            wtype = 'edge' if i % 2 == 0 else 'cloud'
            workers.append({
                'id': f'w{i}',
                'type': wtype,
                'role': 'trainer',
                'replica': 1,
                'resources': {'memory': '1Gi', 'cpu': '0.5'}
            })
        
        return workers
    
    def deploy(self, yaml_path: str, num_workers: int = 2) -> str:
        """Deploy job"""
        
        if not os.path.exists(yaml_path):
            raise FileNotFoundError(f'YAML not found: {yaml_path}')
        
        if not isinstance(num_workers, int) or num_workers < 1:
            raise ValueError('Invalid worker count')
        
        with open(yaml_path, 'r') as f:
            spec = yaml.safe_load(f)
        
        job_id = f'job_{uuid.uuid4().hex[:8]}'
        job_name = spec['metadata']['name']
        
        try:
            workers = self._create_workers(job_id, job_name, num_workers, spec)
            
            meta = {
                'job_id': job_id,
                'job_name': job_name,
                'yaml_path': yaml_path,
                'deployed_at': datetime.now().isoformat(),
                'workers': workers,
                'status': 'running'
            }
            
            self.jobs[job_id] = meta
            self._save_meta(job_id, meta)
            
            logger.info(f'Job deployed: {job_id} ({num_workers} workers)')
            return job_id
        
        except Exception as e:
            logger.error(f'Deployment failed: {e}')
            raise JobDeploymentError(f'Job deployment failed: {e}')
    
    def _create_workers(self, job_id: str, job_name: str,
                       num: int, spec: Dict) -> List[Dict]:
        """Create worker containers"""
        workers = []
        net_name = f'{self.env_id}_net'
        
        for i in range(num):
            wid = f'{job_id}_w{i}'
            wtype = 'edge' if i % 2 == 0 else 'cloud'
            
            try:
                image = spec['spec']['algorithm']['image']
                
                try:
                    self.docker_client.images.get(image)
                except docker.errors.ImageNotFound:
                    logger.error(f'Image not found: {image}')
                    raise
                
                wvol = os.path.join(self.env_dir, 'workers', wid)
                os.makedirs(wvol, mode=0o700, exist_ok=True)
                
                token = generate_token()
                
                cont = self.docker_client.containers.run(
                    image=image,
                    name=wid,
                    environment={
                        'WORKER_ID': wid,
                        'WORKER_TYPE': wtype,
                        'JOB_ID': job_id,
                        'JOB_NAME': job_name,
                        'WORKER_TOKEN': token
                    },
                    volumes=[f'{wvol}:/data'],
                    network=net_name,
                    detach=True,
                    mem_limit='1g',
                    restart_policy={'Name': 'on-failure', 'MaximumRetryCount': 3}
                )
                
                workers.append({
                    'container_id': cont.id,
                    'worker_id': wid,
                    'worker_type': wtype,
                    'status': 'running',
                    'token': token
                })
                
                logger.info(f'Worker created: {wid}')
            
            except docker.errors.APIError as e:
                logger.error(f'Worker creation failed: {e}')
                raise JobDeploymentError(f'Worker creation failed: {e}')
        
        return workers
    
    def delete(self, job_id: str) -> bool:
        """Delete job"""
        if job_id not in self.jobs:
            logger.warning(f'Job not found: {job_id}')
            return False
        
        try:
            meta = self.jobs[job_id]
            
            if job_id in self.monitors:
                self.monitors[job_id]['stop'].set()
                self.monitors[job_id]['thread'].join(timeout=5)
                del self.monitors[job_id]
            
            for w in meta.get('workers', []):
                try:
                    cont = self.docker_client.containers.get(w['container_id'])
                    cont.stop(timeout=10)
                    cont.remove()
                    logger.info(f'Worker removed: {w["worker_id"]}')
                except (docker.errors.NotFound, docker.errors.APIError):
                    pass
            
            meta['status'] = 'deleted'
            self._save_meta(job_id, meta)
            
            del self.jobs[job_id]
            logger.info(f'Job deleted: {job_id}')
            return True
        
        except Exception as e:
            logger.error(f'Delete failed: {e}')
            return False
    
    def list(self) -> List[Dict]:
        """List jobs"""
        return [
            {
                'job_id': jid,
                'name': m['job_name'],
                'status': m['status'],
                'workers': len(m['workers']),
                'deployed': m['deployed_at']
            }
            for jid, m in self.jobs.items()
        ]
    
    def watch(self, job_id: str, callback: Optional[Callable] = None) -> None:
        """Watch job results"""
        if job_id not in self.jobs:
            raise ValueError(f'Job not found: {job_id}')
        
        stop = threading.Event()
        thr = threading.Thread(
            target=self._monitor,
            args=(job_id, callback, stop),
            daemon=False
        )
        thr.start()
        
        self.monitors[job_id] = {'thread': thr, 'stop': stop}
        logger.info(f'Watching job: {job_id}')
    
    def _monitor(self, job_id: str, callback: Optional[Callable],
                stop: threading.Event) -> None:
        """Monitor job"""
        meta = self.jobs[job_id]
        res_dir = os.path.join(self.env_dir, 'results', job_id)
        os.makedirs(res_dir, exist_ok=True)
        
        while not stop.is_set():
            try:
                for w in meta['workers']:
                    try:
                        cont = self.docker_client.containers.get(w['container_id'])
                        logs = cont.logs(tail=20).decode('utf-8')
                        metrics = self._parse_metrics(logs)
                        
                        if metrics:
                            res = {
                                'timestamp': datetime.now().isoformat(),
                                'worker_id': w['worker_id'],
                                'metrics': metrics
                            }
                            
                            res_file = os.path.join(res_dir, f"{w['worker_id']}.json")
                            with open(res_file, 'w') as f:
                                json.dump(res, f)
                            os.chmod(res_file, 0o600)
                            
                            if callback:
                                callback(res)
                    
                    except docker.errors.NotFound:
                        pass
                
                meta['status'] = self._get_status(meta)
                time.sleep(5)
            
            except Exception as e:
                logger.error(f'Monitor error: {e}')
                time.sleep(5)
    
    def _parse_metrics(self, logs: str) -> Optional[Dict]:
        """Parse metrics from logs"""
        metrics = {}
        for line in logs.split('\n'):
            if 'accuracy:' in line.lower():
                try:
                    metrics['accuracy'] = float(line.split(':')[1].strip())
                except (ValueError, IndexError):
                    pass
            elif 'loss:' in line.lower():
                try:
                    metrics['loss'] = float(line.split(':')[1].strip())
                except (ValueError, IndexError):
                    pass
        
        return metrics if metrics else None
    
    def _get_status(self, meta: Dict) -> str:
        """Get job status"""
        all_running = True
        for w in meta['workers']:
            try:
                cont = self.docker_client.containers.get(w['container_id'])
                if cont.status != 'running':
                    all_running = False
                    break
            except docker.errors.NotFound:
                all_running = False
        
        return 'running' if all_running else 'completed'
    
    def _save_meta(self, job_id: str, meta: Dict) -> None:
        """Save metadata"""
        meta_file = os.path.join(self.env_dir, 'jobs', f'{job_id}.json')
        os.makedirs(os.path.dirname(meta_file), exist_ok=True)
        
        with open(meta_file, 'w') as f:
            json.dump(meta, f, indent=2)
        os.chmod(meta_file, 0o600)
    
    def get_results(self, job_id: str) -> Dict:
        """Get job results"""
        if job_id not in self.jobs:
            raise ValueError(f'Job not found: {job_id}')
        
        res_dir = os.path.join(self.env_dir, 'results', job_id)
        res = {'job_id': job_id, 'workers': {}}
        
        if os.path.exists(res_dir):
            for res_file in os.listdir(res_dir):
                if res_file.endswith('.json'):
                    with open(os.path.join(res_dir, res_file), 'r') as f:
                        res['workers'][res_file] = json.load(f)
        
        return res