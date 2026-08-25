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

import hashlib
import secrets
import os
from typing import Tuple


def generate_secure_password(length: int = 24) -> str:
    """Generate cryptographically secure random password"""
    return secrets.token_urlsafe(length)


def hash_password(password: str, salt: str = None) -> Tuple[str, str]:
    """Hash password using PBKDF2 with SHA256"""
    if not salt:
        salt = secrets.token_hex(16)
    
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    
    return pwd_hash.hex(), salt


def verify_password(password: str, pwd_hash: str, salt: str) -> bool:
    """Verify password against hash"""
    computed_hash, _ = hash_password(password, salt)
    return computed_hash == pwd_hash


def generate_token(length: int = 32) -> str:
    """Generate secure random token"""
    return secrets.token_urlsafe(length)


def generate_api_key(prefix: str = "sk") -> str:
    """Generate API key with prefix"""
    random_part = secrets.token_urlsafe(32)
    return f"{prefix}_{random_part}"


def sanitize_path(path: str) -> str:
    """Sanitize file path to prevent traversal attacks"""
    if '..' in path or path.startswith('/'):
        raise ValueError(f"Invalid path: {path}")
    return path


def validate_environment_variable(key: str, default: str = None) -> str:
    """Get environment variable safely"""
    value = os.getenv(key, default)
    if not value:
        raise ValueError(f"Environment variable not set: {key}")
    return value