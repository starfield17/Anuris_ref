from dataclasses import asdict, dataclass
from pathlib import Path

import toml

from .prompts import DEFAULT_SYSTEM_PROMPT


@dataclass
class Config:
    """Declarative configuration class."""

    api_key: str = ""
    proxy: str = ""
    model: str = ""
    debug: bool = False
    base_url: str = ""
    temperature: float = 0.4
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Create Config instance from dictionary."""
        return cls(**{key: value for key, value in data.items() if key in cls.__annotations__})

    def to_dict(self) -> dict:
        """Convert Config to dictionary."""
        return {key: value for key, value in asdict(self).items()}


class ConfigManager:
    """Manages configuration storage and retrieval (TOML version)."""

    def __init__(self):
        self.config_file = Path.home() / ".anuris_config.toml"
        self.default_config = Config()

    def save_config(self, **kwargs) -> None:
        """Save configuration to hidden TOML file in user's home directory."""
        try:
            config = self.load_config()
            config_dict = config.to_dict()

            for key, value in kwargs.items():
                if value is not None and key in config_dict:
                    config_dict[key] = value

            with open(self.config_file, "w", encoding="utf-8") as file_obj:
                toml.dump(config_dict, file_obj)

            self.config_file.chmod(0o600)
        except Exception as exc:
            raise Exception(f"Failed to save config: {str(exc)}") from exc

    def load_config(self) -> Config:
        """Load configuration from hidden TOML file."""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as file_obj:
                    config_dict = toml.load(file_obj)
                combined_config = {**self.default_config.to_dict(), **config_dict}
                return Config.from_dict(combined_config)
            return self.default_config
        except Exception as exc:
            raise Exception(f"Failed to load config: {str(exc)}") from exc
