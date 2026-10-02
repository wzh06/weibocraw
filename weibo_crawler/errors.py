class CrawlStopped(RuntimeError):
    """The page requires a manual action such as login or verification."""


class ConfigurationError(ValueError):
    """The YAML configuration is invalid."""
