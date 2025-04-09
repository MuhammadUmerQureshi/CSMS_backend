from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect
import json
import asyncio
import inspect
import time
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional
from chargePoint import ChargePoint201
from utils import convert_to_dict, camel_to_snake, setup_logger, ErrorCode, CALL, CALLRESULT, CALLERROR

# Set up logging
logger = setup_logger("ocpp_handler")

# Global dictionary to maintain active connections
active_chargers: Dict[str, 'OCPPServerConnection'] = {}

class OCPPServerConnection:
    """
    Handles the OCPP connection for a charging station
    """
    def __init__(self, websocket: WebSocket, charger_id: str):
        """
        Initialize the connection handler
        
        Args:
            websocket: The WebSocket connection
            charger_id: The ID of the charger
        """
        self.websocket = websocket
        self.charger_id = charger_id
        self.last_seen = time.time()
        self.message_queue = asyncio.Queue()
        self.is_active = True
        self.is_available = True
        self.status = "Available"
        self.connected_at = datetime.now()
        
        # Instantiate ChargePoint201 with required parameters
        self.cp = ChargePoint201(charger_id, self.send_message, iso15118_certs=None)
        logger.info(f"Created connection handler for charger {charger_id}")
        
    async def send_message(self, message: List[Any]) -> None:
        """
        Send message to charging station
        
        Args:
            message: The OCPP message to send
        """
        if not self.is_active:
            logger.warning(f"Attempted to send message to inactive charger {self.charger_id}")
            return
            
        try:
            msg_json = json.dumps(message)
            logger.info(f"Sending to {self.charger_id}: {msg_json}")
            await self.websocket.send_text(msg_json)
            self.last_seen = time.time()
        except Exception as e:
            logger.error(f"Error sending message to {self.charger_id}: {e}", exc_info=True)
            self.is_active = False
            
    async def start(self) -> None:
        """
        Start message processing tasks
        """
        self.message_task = asyncio.create_task(self.message_handler())
        self.queue_task = asyncio.create_task(self.queue_processor())
        
    async def stop(self) -> None:
        """
        Stop message processing tasks
        """
        self.is_active = False
        if hasattr(self, 'message_task'):
            self.message_task.cancel()
        if hasattr(self, 'queue_task'):
            self.queue_task.cancel()
        
    async def queue_message(self, message: List[Any]) -> None:
        """
        Queue a message to be sent to the charging station
        
        Args:
            message: The OCPP message to queue
        """
        await self.message_queue.put(message)
        
    async def queue_processor(self) -> None:
        """
        Process queued messages
        """
        try:
            while self.is_active:
                if not self.message_queue.empty():
                    message = await self.message_queue.get()
                    await self.send_message(message)
                    self.message_queue.task_done()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info(f"Queue processor for {self.charger_id} cancelled")
        except Exception as e:
            logger.error(f"Error in queue processor for {self.charger_id}: {e}", exc_info=True)
            
    async def message_handler(self) -> None:
        """
        Handle incoming OCPP messages
        """
        try:
            while self.is_active:
                # Wait for a message
                data = await self.websocket.receive_text()
                self.last_seen = time.time()
                logger.info(f"Received from {self.charger_id}: {data}")
                
                # Parse the message
                try:
                    message = json.loads(data)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON received from {self.charger_id}: {e}")
                    await self.send_message([CALLERROR, "", ErrorCode.FORMATION_VIOLATION, "Invalid JSON format", {}])
                    continue
                
                # Check message format
                if not isinstance(message, list) or len(message) < 3:
                    logger.error(f"Invalid message format from {self.charger_id}: {message}")
                    await self.send_message([CALLERROR, "", ErrorCode.FORMATION_VIOLATION, "Invalid message format", {}])
                    continue
                
                # Extract message components
                msg_type = message[0]
                msg_id = message[1]
                
                # Handle message based on type
                if msg_type == CALL:
                    await self.handle_call(message)
                elif msg_type == CALLRESULT:
                    await self.handle_call_result(message)
                elif msg_type == CALLERROR:
                    await self.handle_call_error(message)
                else:
                    logger.warning(f"Unknown message type from {self.charger_id}: {msg_type}")
                    await self.send_message([CALLERROR, msg_id, ErrorCode.FORMATION_VIOLATION, f"Unknown message type: {msg_type}", {}])
                
        except WebSocketDisconnect:
            logger.info(f"Charger {self.charger_id} disconnected")
        except asyncio.CancelledError:
            logger.info(f"Message handler for {self.charger_id} cancelled")
        except Exception as e:
            logger.error(f"Error in message handler for {self.charger_id}: {e}", exc_info=True)
        finally:
            self.is_active = False
            
    async def handle_call(self, message: List[Any]) -> None:
        """
        Handle CALL messages from the charging station
        
        Args:
            message: The OCPP CALL message
        """
        msg_id = message[1]
        action = message[2]
        payload = message[3] if len(message) > 3 else {}
        
        logger.info(f"Processing {action} request from {self.charger_id} with ID {msg_id}")
        
        # Convert action to method name (e.g. BootNotification -> on_boot_notification)
        handler_name = f"on_{camel_to_snake(action)}"
        
        # Check if handler exists
        if hasattr(self.cp, handler_name):
            try:
                # Get the handler method
                handler = getattr(self.cp, handler_name)
                logger.info(f"Found handler {handler_name} for {action}")
                
                # Check what parameters the handler expects
                sig = inspect.signature(handler)
                logger.debug(f"Handler signature for {action}: {sig}")
                
                # Filter payload to only include expected parameters
                filtered_payload = {}
                for param_name in sig.parameters:
                    if param_name != 'self' and param_name in payload:
                        filtered_payload[param_name] = payload[param_name]
                
                # If there are **kwargs in the signature, include all payload
                if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()):
                    filtered_payload = payload
                
                logger.debug(f"Filtered payload for {action}: {filtered_payload}")
                
                # Update status for certain messages
                if action == "StatusNotification":
                    if "status" in payload:
                        self.status = payload["status"]
                    if "connector_status" in payload and payload["connector_status"] == "Available":
                        self.is_available = True
                    elif "connector_status" in payload:
                        self.is_available = False
                
                # Call the handler with the filtered payload
                response = handler(**filtered_payload)
                logger.info(f"Response from handler for {action}: {response}")
                
                # Convert response to dictionary
                response_dict = convert_to_dict(response)
                logger.debug(f"Response dict for {action}: {response_dict}")
                
                # Send response
                response_message = [CALLRESULT, msg_id, response_dict]
                await self.send_message(response_message)
                logger.info(f"Sent response for {action} to {self.charger_id}")
                
            except Exception as e:
                logger.error(f"Error handling {action} from {self.charger_id}: {e}", exc_info=True)
                error_message = [CALLERROR, msg_id, ErrorCode.INTERNAL_ERROR, str(e), {}]
                await self.send_message(error_message)
        else:
            logger.warning(f"No handler '{handler_name}' for action: {action} from {self.charger_id}")
            error_message = [CALLERROR, msg_id, ErrorCode.NOT_IMPLEMENTED, f"Action {action} not supported", {}]
            await self.send_message(error_message)
            
    async def handle_call_result(self, message: List[Any]) -> None:
        """
        Handle CALLRESULT messages from the charging station
        
        Args:
            message: The OCPP CALLRESULT message
        """
        msg_id = message[1]
        payload = message[2]
        
        logger.info(f"Received CALLRESULT {msg_id} from {self.charger_id}")
        
        try:
            # Pass to ChargePoint instance
            await self.cp._handle_call_result(msg_id, payload)
        except Exception as e:
            logger.error(f"Error handling CALLRESULT {msg_id} from {self.charger_id}: {e}", exc_info=True)
            
    async def handle_call_error(self, message: List[Any]) -> None:
        """
        Handle CALLERROR messages from the charging station
        
        Args:
            message: The OCPP CALLERROR message
        """
        msg_id = message[1]
        error_code = message[2]
        error_description = message[3]
        error_details = message[4] if len(message) > 4 else {}
        
        logger.info(f"Received CALLERROR {msg_id} from {self.charger_id}: {error_code} - {error_description}")
        
        try:
            # Pass to ChargePoint instance
            await self.cp._handle_call_error(msg_id, error_code, error_description, error_details)
        except Exception as e:
            logger.error(f"Error handling CALLERROR {msg_id} from {self.charger_id}: {e}", exc_info=True)
            
    async def send_heartbeat_request(self) -> None:
        """
        Send a Heartbeat request to the charging station
        """
        msg_id = str(uuid.uuid4())
        await self.send_message([CALL, msg_id, "Heartbeat", {}])
        
    async def send_reset_request(self, reset_type: str) -> None:
        """
        Send a Reset request to the charging station
        
        Args:
            reset_type: Type of reset (Hard or Soft)
        """
        msg_id = str(uuid.uuid4())
        await self.send_message([CALL, msg_id, "Reset", {"type": reset_type}])
        
    async def send_get_variables_request(self, component_name: str, variable_name: str) -> None:
        """
        Send a GetVariables request to the charging station
        
        Args:
            component_name: Name of the component
            variable_name: Name of the variable
        """
        await self.cp.get_config_variables_req(component_name, variable_name)
        
    async def send_set_variables_request(self, component_name: str, variable_name: str, value: Any) -> None:
        """
        Send a SetVariables request to the charging station
        
        Args:
            component_name: Name of the component
            variable_name: Name of the variable
            value: Value to set
        """
        await self.cp.set_config_variables_req(component_name, variable_name, value)