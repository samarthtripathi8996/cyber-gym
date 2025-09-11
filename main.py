from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import asyncio
import json,tempfile
import psutil
import logging
import random
import subprocess
import os
from typing import Dict, List
import threading
import time
import google.generativeai as genai
from fastapi.responses import HTMLResponse, JSONResponse
import subprocess, json
app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Test mode - set to True to simulate VMs locally
TEST_MODE = True

# Configuration - Update these with your VM details when TEST_MODE = False
VM_CONFIG = {
    "vm1": {
        "host": "",  # Replace with VM1 IP
        "port": 22,
        "username": "",  # Replace with your username
        "password": "",  # Replace with your password or use key
        "name": "VM1"
    },
    "vm2": {
       "host": "",  # Replace with VM1 IP
        "port": 22,
        "username": "",  # Replace with your username
        "password": "",  # Replace with your password or use key
        "name": "VM2"
    }
}

# Store active connections
active_connections: Dict[str, List[WebSocket]] = {"vm1": [], "vm2": []}

# For test mode
local_shells: Dict[str, subprocess.Popen] = {}

# For real SSH mode
try:
    import paramiko
    ssh_clients: Dict[str, paramiko.SSHClient] = {}
    ssh_channels: Dict[str, paramiko.Channel] = {}
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False
    ssh_clients = {}
    ssh_channels = {}

def create_ssh_connection(vm_id: str):
    """Create SSH connection to VM"""
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        config = VM_CONFIG[vm_id]
        client.connect(
            hostname=config["host"],
            port=config["port"],
            username=config["username"],
            password=config["password"]
        )
        
        # Create interactive shell
        channel = client.invoke_shell(term='xterm', width=80, height=24)
        
        ssh_clients[vm_id] = client
        ssh_channels[vm_id] = channel
        
        return True
    except Exception as e:
        logging.error(f"Failed to connect to {vm_id}: {e}")
        return False

def get_system_stats(vm_id: str):
    """Get system statistics from VM"""
    try:
        client = ssh_clients.get(vm_id)
        if not client:
            return None
            
        # Get CPU usage
        stdin, stdout, stderr = client.exec_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
        cpu_usage = stdout.read().decode().strip()
        
        # Get memory usage
        stdin, stdout, stderr = client.exec_command("free | grep Mem | awk '{print ($3/$2) * 100.0}'")
        memory_usage = stdout.read().decode().strip()
        
        # Get disk usage
        stdin, stdout, stderr = client.exec_command("df -h / | awk 'NR==2{print $5}' | cut -d'%' -f1")
        disk_usage = stdout.read().decode().strip()
        
        # Get load average
        stdin, stdout, stderr = client.exec_command("uptime | awk -F'load average:' '{print $2}' | cut -d',' -f1")
        load_avg = stdout.read().decode().strip()
        
        return {
            "cpu": float(cpu_usage) if cpu_usage.replace('.', '').isdigit() else 0,
            "memory": float(memory_usage) if memory_usage.replace('.', '').isdigit() else 0,
            "disk": float(disk_usage) if disk_usage.isdigit() else 0,
            "load": float(load_avg) if load_avg.replace('.', '').isdigit() else 0
        }
    except Exception as e:
        logging.error(f"Error getting stats for {vm_id}: {e}")
        return None

async def read_from_ssh(vm_id: str):
    """Read output from SSH channel and send to WebSocket clients"""
    channel = ssh_channels.get(vm_id)
    if not channel:
        return
        
    while True:
        try:
            if channel.recv_ready():
                data = channel.recv(1024).decode('utf-8', errors='ignore')
                if data:
                    # Send to all connected clients for this VM
                    for websocket in active_connections[vm_id][:]:
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "terminal_output",
                                "data": data
                            }))
                        except:
                            active_connections[vm_id].remove(websocket)
            await asyncio.sleep(0.01)
        except Exception as e:
            logging.error(f"Error reading from SSH {vm_id}: {e}")
            break

@app.websocket("/ws/terminal/{vm_id}")
async def terminal_websocket(websocket: WebSocket, vm_id: str):
    await websocket.accept()
    
    if vm_id not in VM_CONFIG:
        await websocket.close(code=1000)
        return
    
    # Add to active connections
    active_connections[vm_id].append(websocket)
    
    # Create SSH connection if not exists
    if vm_id not in ssh_clients:
        if not create_ssh_connection(vm_id):
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Failed to connect to {vm_id}"
            }))
            await websocket.close()
            return
        
        # Start reading from SSH in background
        asyncio.create_task(read_from_ssh(vm_id))
    
    try:
        while True:
            # Receive input from client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "terminal_input":
                channel = ssh_channels.get(vm_id)
                if channel:
                    channel.send(message["data"])
                    
    except WebSocketDisconnect:
        active_connections[vm_id].remove(websocket)

@app.websocket("/ws/stats/{vm_id}")
async def stats_websocket(websocket: WebSocket, vm_id: str):
    await websocket.accept()
    
    if vm_id not in VM_CONFIG:
        await websocket.close(code=1000)
        return
    
    try:
        while True:
            stats = get_system_stats(vm_id)
            if stats:
                await websocket.send_text(json.dumps({
                    "type": "stats",
                    "data": stats
                }))
            await asyncio.sleep(2)  # Update every 2 seconds
            
    except WebSocketDisconnect:
        pass

# Serve the main HTML file
@app.get("/")
async def get_index():
    return FileResponse('static/index.html')
genai.configure(api_key="")
model = genai.GenerativeModel("gemini-1.5-flash")
@app.get("/security-check")
def security_check():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    config = VM_CONFIG["vm1"]  # Change to "vm2" to analyze VM2
    ssh.connect(
        hostname=config["host"],
        port=config["port"],
        username=config["username"],
        password=config["password"]
        )
        

    # Run log collection script on victim
    ssh.exec_command("bash ~/ai/collect_logs_ai.sh")

    # Fetch JSON file back to backend
    sftp = ssh.open_sftp()
    local_tmp = tempfile.NamedTemporaryFile(delete=False)
    sftp.get("/home/server/system_logs/system_logs_ai.json", local_tmp.name)
    sftp.close()
    ssh.close()

    # Load logs
    with open(local_tmp.name) as f:
        logs = json.load(f)

    # Ask Gemini for a structured report
    prompt = """
    You are a cybersecurity analyst. Analyze the system logs.
    Respond ONLY in valid JSON with the following structure:
    {
      "attack_detected": "short summary of the attack",
      "evidence": ["list of evidence points"],
      "recommendations": ["list of security fixes"]
    }
    """

    response = model.generate_content([prompt, json.dumps(logs)])

    # Try to parse as JSON
    try:
        report = json.loads(response.text)
    except:
        report = {"attack_detected": "Parsing error",
                  "evidence": [response.text],
                  "recommendations": []}

    return JSONResponse(report)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)