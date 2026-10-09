from uuid import uuid4


class NotiDoError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 409,
        fields: list[str] | None = None,
        retryable: bool = False,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.fields, self.retryable = fields or [], retryable
        self.trace_id = str(uuid4())
        self.details = details or {}

    def public(self):
        return {
            "code": self.code,
            "safe_message": self.message,
            "retryable": self.retryable,
            "trace_id": self.trace_id,
            "blocking_fields": self.fields,
            "details": self.details,
        }
