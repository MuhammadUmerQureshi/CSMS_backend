from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState
import asyncio
import logging
import time
from datetime import datetime
from ocpp_handler import OCPPServerConnection, active_chargers
from utils import setup_logger

# Set up logging
logger = setup_logger("server")

app = FastAPI(
    title="OCPP Server",
    description="A FastAPI server implementing OCPP 2.0.1 protocol",
    version="1.0.0"
)

# Add CORS middleware for the REST API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ocpp/{charger_id}")
async def websocket_endpoint(websocket: WebSocket, charger_id: str):
    """
    WebSocket endpoint for OCPP connections
    
    Args:
        websocket: The WebSocket connection
        charger_id: The ID of the charger
    """
    # Accept the connection
    logger.info(f"New connection request from charger {charger_id}")
    
    # Verify the subprotocol
    requested_protocols = websocket.headers.get("sec-websocket-protocol", "")
    logger.info(f"Requested protocols from {charger_id}: {requested_protocols}")
    
    if "ocpp2.0.1" not in requested_protocols:
        logger.warning(f"Charger {charger_id} requested unsupported protocol: {requested_protocols}")
        await websocket.close(code=1002)  # Protocol not supported
        return

    # Accept the connection with the appropriate subprotocol
    await websocket.accept(subprotocol="ocpp2.0.1")
    logger.info(f"Accepted connection from charger {charger_id} with ocpp2.0.1 protocol")
    
    # Check if this charger is already connected
    if charger_id in active_chargers and active_chargers[charger_id].is_active:
        logger.warning(f"Charger {charger_id} is already connected - closing old connection")
        await active_chargers[charger_id].stop()
    
    # Create a new connection handler for this charger
    connection = OCPPServerConnection(websocket, charger_id)
    
    # Add the connection to our active chargers list
    active_chargers[charger_id] = connection
    logger.info(f"Added {charger_id} to active connections. Total active: {len(active_chargers)}")
    
    try:
        # Start the message handler for this connection
        await connection.start()
        
        # Keep the connection alive
        while websocket.client_state != WebSocketState.DISCONNECTED and connection.is_active:
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        logger.info(f"Charger {charger_id} disconnected")
    except Exception as e:
        logger.error(f"Error with charger {charger_id}: {e}", exc_info=True)
    finally:
        # Clean up the connection
        await connection.stop()
        
        # Only remove from active chargers if it's the same instance
        if charger_id in active_chargers and active_chargers[charger_id] is connection:
            del active_chargers[charger_id]
            logger.info(f"Removed {charger_id} from active connections. Remaining active: {len(active_chargers)}")

# REST API endpoints to manage chargers

@app.get("/")
async def root():
    """
    Root endpoint
    
    Returns:
        Basic server information
    """
    return {
        "name": "OCPP Server",
        "version": "1.0.0",
        "description": "A FastAPI server implementing OCPP 2.0.1 protocol"
    }

@app.get("/chargers")
async def get_chargers():
    """
    Get list of connected chargers
    
    Returns:
        List of connected chargers with status information
    """
    result = []
    for charger_id, connection in active_chargers.items():
        result.append({
            "id": charger_id,
            "status": connection.status,
            "available": connection.is_available,
            "connected_since": connection.connected_at.isoformat(),
            "last_seen": datetime.fromtimestamp(connection.last_seen).isoformat(),
        })
    return result

@app.get("/chargers/{charger_id}")
async def get_charger(charger_id: str):
    """
    Get information about a specific charger
    
    Args:
        charger_id: The ID of the charger
        
    Returns:
        Status information for the specified charger
    """
    if charger_id not in active_chargers:
        raise HTTPException(status_code=404, detail=f"Charger {charger_id} not found")
        
    connection = active_chargers[charger_id]
    return {
        "id": charger_id,
        "status": connection.status,
        "available": connection.is_available,
        "connected_since": connection.connected_at.isoformat(),
        "last_seen": datetime.fromtimestamp(connection.last_seen).isoformat(),
    }

@app.post("/chargers/{charger_id}/reset")
async def reset_charger(charger_id: str, reset_type: str = "Soft"):
    """
    Reset a charger
    
    Args:
        charger_id: The ID of the charger
        reset_type: Type of reset (Hard or Soft)
        
    Returns:
        Confirmation message
    """
    if charger_id not in active_chargers:
        raise HTTPException(status_code=404, detail=f"Charger {charger_id} not found")
        
    if not active_chargers[charger_id].is_active:
        raise HTTPException(status_code=400, detail=f"Charger {charger_id} is not active")
        
    if reset_type not in ["Hard", "Soft"]:
        raise HTTPException(status_code=400, detail="Reset type must be 'Hard' or 'Soft'")
        
    await active_chargers[charger_id].send_reset_request(reset_type)
    return {"message": f"{reset_type} reset initiated for charger {charger_id}"}

@app.post("/chargers/{charger_id}/heartbeat")
async def send_heartbeat(charger_id: str):
    """
    Send a heartbeat request to a charger
    
    Args:
        charger_id: The ID of the charger
        
    Returns:
        Confirmation message
    """
    if charger_id not in active_chargers:
        raise HTTPException(status_code=404, detail=f"Charger {charger_id} not found")
        
    if not active_chargers[charger_id].is_active:
        raise HTTPException(status_code=400, detail=f"Charger {charger_id} is not active")
        
    await active_chargers[charger_id].send_heartbeat_request()
    return {"message": f"Heartbeat sent to charger {charger_id}"}

@app.post("/chargers/{charger_id}/getvariable")
async def get_variable(charger_id: str, component_name: str, variable_name: str):
    """
    Get a variable from a charger
    
    Args:
        charger_id: The ID of the charger
        component_name: Name of the component
        variable_name: Name of the variable
        
    Returns:
        Confirmation message
    """
    if charger_id not in active_chargers:
        raise HTTPException(status_code=404, detail=f"Charger {charger_id} not found")
        
    if not active_chargers[charger_id].is_active:
        raise HTTPException(status_code=400, detail=f"Charger {charger_id} is not active")
        
    await active_chargers[charger_id].send_get_variables_request(component_name, variable_name)
    return {"message": f"GetVariables request sent to charger {charger_id}"}

@app.post("/chargers/{charger_id}/setvariable")
async def set_variable(charger_id: str, component_name: str, variable_name: str, value: str):
    """
    Set a variable on a charger
    
    Args:
        charger_id: The ID of the charger
        component_name: Name of the component
        variable_name: Name of the variable
        value: Value to set
        
    Returns:
        Confirmation message
    """
    if charger_id not in active_chargers:
        raise HTTPException(status_code=404, detail=f"Charger {charger_id} not found")
        
    if not active_chargers[charger_id].is_active:
        raise HTTPException(status_code=400, detail=f"Charger {charger_id} is not active")
        
    await active_chargers[charger_id].send_set_variables_request(component_name, variable_name, value)
    return {"message": f"SetVariables request sent to charger {charger_id}"}

# Background task to monitor connections
@app.on_event("startup")
async def startup_event():
    """
    Startup event handler
    """
    asyncio.create_task(monitor_connections())

async def monitor_connections():
    """
    Monitor connections and clean up inactive ones
    """
    while True:
        try:
            current_time = time.time()
            for charger_id in list(active_chargers.keys()):
                connection = active_chargers[charger_id]
                # Check if connection is too old without activity
                if current_time - connection.last_seen > 300:  # 5 minutes
                    logger.warning(f"Charger {charger_id} has been inactive for too long, closing connection")
                    await connection.stop()
                    if charger_id in active_chargers:
                        del active_chargers[charger_id]
            
            await asyncio.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"Error in connection monitor: {e}", exc_info=True)
            await asyncio.sleep(60)  # Try again in a minute

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting OCPP server")
    uvicorn.run(app, host="127.0.0.1", port=8000)