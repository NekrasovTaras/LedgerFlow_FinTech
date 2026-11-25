from typing import Any


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(message)

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }
