import logging
from typing import Any, Dict

# Constants for OCPP message types
CALL = 2
CALLRESULT = 3
CALLERROR = 4

# Error codes for CALLERROR messages
class ErrorCode:
    NOT_IMPLEMENTED = "NotImplemented"
    NOT_SUPPORTED = "NotSupported"
    INTERNAL_ERROR = "InternalError"
    PROTOCOL_ERROR = "ProtocolError"
    SECURITY_ERROR = "SecurityError"
    FORMATION_VIOLATION = "FormationViolation"
    PROPERTY_CONSTRAINT_VIOLATION = "PropertyConstraintViolation"
    OCCURRENCE_CONSTRAINT_VIOLATION = "OccurrenceConstraintViolation"
    TYPE_CONSTRAINT_VIOLATION = "TypeConstraintViolation"
    GENERIC_ERROR = "GenericError"

def setup_logger(name: str) -> logging.Logger:
    """
    Set up a logger with the given name
    
    Args:
        name: Name of the logger
        
    Returns:
        The configured logger
    """
    logger = logging.getLogger(name)
    
    # Only configure if no handlers exist
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Create handlers
        console_handler = logging.StreamHandler()
        file_handler = logging.FileHandler(f"{name}.log")
        
        # Set level
        console_handler.setLevel(logging.INFO)
        file_handler.setLevel(logging.INFO)
        
        # Create formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Add formatter to handlers
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)
        
        # Add handlers to logger
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
    
    return logger

def convert_to_dict(obj: Any) -> Any:
    """
    Convert response objects to dictionaries for OCPP JSON response
    
    Args:
        obj: The object to convert
        
    Returns:
        The object converted to a JSON-serializable format
    """
    if obj is None:
        return None
    
    # Handle basic types
    if isinstance(obj, (str, int, float, bool)):
        return obj
    
    # Handle enum types (they have a .value attribute)
    if hasattr(obj, 'value'):
        return obj.value
    
    # Handle list types
    if isinstance(obj, list):
        return [convert_to_dict(item) for item in obj]
    
    # Handle dictionary types
    if isinstance(obj, dict):
        return {k: convert_to_dict(v) for k, v in obj.items()}
    
    # Handle objects with __dict__ (like dataclasses, which the response objects appear to be)
    if hasattr(obj, '__dict__'):
        result = {}
        for key, value in obj.__dict__.items():
            # Skip None values and special attributes
            if value is not None and not key.startswith('_') and key != 'custom_data':
                result[key] = convert_to_dict(value)
        return result
    
    # Fallback for other types
    return str(obj)

def camel_to_snake(text: str) -> str:
    """
    Convert camelCase to snake_case
    
    Args:
        text: CamelCase text
        
    Returns:
        snake_case text
    """
    return ''.join(['_' + c.lower() if c.isupper() else c for c in text]).lstrip('_')

def snake_to_camel(text: str) -> str:
    """
    Convert snake_case to camelCase
    
    Args:
        text: snake_case text
        
    Returns:
        camelCase text
    """
    components = text.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])