"""Доменные ошибки агента."""


class WikiAgentError(RuntimeError):
    """Базовая контролируемая ошибка агента."""


class ConfigurationError(WikiAgentError):
    """Некорректная или неполная конфигурация."""


class ProviderUnavailableError(WikiAgentError):
    """LLM-провайдер не настроен или недоступен."""


class SecurityError(WikiAgentError):
    """Попытка выйти за разрешённые файловые границы."""


class ValidationError(WikiAgentError):
    """Данные или предлагаемые изменения не прошли проверку."""


class ProposalError(WikiAgentError):
    """Proposal отсутствует, повреждён или не может быть применён."""
