"""Custom exception definitions."""


class ControllerError(Exception):
    """Raised when the controller fails to route a request."""


class ParamElicitationError(Exception):
    """Raised when required parameters are missing and cannot be obtained."""


class MCPAuthorizationError(Exception):
    """Raised when MCP auth fails."""


class MCPDispatchError(Exception):
    """Raised when MCP tool execution fails."""

