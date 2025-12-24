import os
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class Config:
    """Ghost Shell Configuration"""
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent
    DATA_DIR: Path = BASE_DIR / 'data'
    LOG_DIR: Path = DATA_DIR / 'logs'
    OUTPUT_DIR: Path = DATA_DIR / 'output'
    
    # Server
    HOST: str = "0.0.0.0"
    HTTP_PORT: int = 8000
    HTTPS_PORT: int = 8444
    
    # Capture
    CAPTURE_MODE: str = "full"
    AGENT_MANAGER_WIDTH: int = 220
    ACTIVATE_WINDOW: bool = False
    
    def __post_init__(self):
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

config = Config()
