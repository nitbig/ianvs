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


class SimulatorException(Exception):
    """Base exception for simulator module"""
    pass


class ConfigurationError(SimulatorException):
    """Raised when configuration is invalid"""
    pass


class EnvironmentError(SimulatorException):
    """Raised when environment validation fails"""
    pass


class HostValidationError(EnvironmentError):
    """Raised when host does not meet requirements"""
    pass


class DockerError(SimulatorException):
    """Raised when Docker operations fail"""
    pass


class JobConfigError(SimulatorException):
    """Raised when job configuration is invalid"""
    pass


class JobDeploymentError(SimulatorException):
    """Raised when job deployment fails"""
    pass


class RedisConnectionError(SimulatorException):
    """Raised when Redis connection fails"""
    pass


class WorkerError(SimulatorException):
    """Raised when worker operations fail"""
    pass