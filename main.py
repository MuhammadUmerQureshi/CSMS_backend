"""
Main entry point for the OCPP Server
"""
import uvicorn
from utils import setup_logger

if __name__ == "__main__":
    logger = setup_logger("main")
    logger.info("Starting OCPP server")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)