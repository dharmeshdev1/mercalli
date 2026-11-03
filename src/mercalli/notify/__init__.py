"""Alerts about the services you depend on, delivered to chat, email, pagers and webhooks."""

from mercalli.notify.config import Config, ConfigError, load_config
from mercalli.notify.notifier import Notifier, NotifyReport

__all__ = ["Config", "ConfigError", "Notifier", "NotifyReport", "load_config"]
