from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        # Check if already in standard error shape
        if isinstance(response.data, dict) and "error" in response.data:
            return response
        
        status_code = response.status_code
        code = "VALIDATION_ERROR"
        if status_code == 404:
            code = "NOT_FOUND"
        elif status_code == 409:
            code = "DUPLICATE"
        elif status_code == 502:
            code = "SUPPLIER_UNAVAILABLE"

        details = []
        if isinstance(response.data, dict):
            for k, v in response.data.items():
                if isinstance(v, list):
                    details.extend([f"{k}: {x}" for x in v])
                else:
                    details.append(f"{k}: {v}")
        elif isinstance(response.data, list):
            details = [str(x) for x in response.data]
        else:
            details = [str(response.data)]

        message = "Validation error" if code == "VALIDATION_ERROR" else "An error occurred"
        if details:
            message = details[0]

        response.data = {
            "error": {
                "code": code,
                "message": message,
                "details": details
            }
        }
    return response

def api_error(code, message, details=None, status_code=status.HTTP_400_BAD_REQUEST):
    return Response(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details if details is not None else []
            }
        },
        status=status_code
    )
