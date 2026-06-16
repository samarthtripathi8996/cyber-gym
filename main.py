from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import asyncio
import json, tempfile
import psutil
import logging
import random
import subprocess
import os
from typing import Dict, List, Optional
import threading
import time
import google.generativeai as genai
from fastapi.responses import HTMLResponse, JSONResponse
import subprocess, json
from pydantic import BaseModel  # add schema for runtime VM configuration
app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Test mode - set to True to simulate VMs locally
TEST_MODE = True

VM_CONFIG = {
    "vm1": {
        "host": "", 
        "port": 22,
        "username": "",  
        "password": "", 
        "name": "VM1"
    },
    "vm2": {
        "host": "",  
        "port": 22,
        "username": "", 
        "password": "",  
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

genai_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if genai_api_key:
    genai.configure(api_key=genai_api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

@app.get("/security-check")
def security_check(vm_id: str = "vm1"):
    if not PARAMIKO_AVAILABLE:
        return JSONResponse({"error": "SSH library not available on server"}, status_code=500)

    if vm_id not in VM_CONFIG:
        return JSONResponse({"error": f"Unknown vm_id '{vm_id}'"}, status_code=400)

    if not model:
        return JSONResponse({"error": "Gemini API key not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY."}, status_code=500)

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        config = VM_CONFIG[vm_id]

        if not all([config.get("host"), config.get("username")]):
            return JSONResponse({"error": "VM host/username not configured on server"}, status_code=400)

        ssh.connect(
            hostname=config["host"],
            port=config.get("port", 22),
            username=config["username"],
            password=config.get("password")
        )

        # Determine remote $HOME
        _, stdout, _ = ssh.exec_command("bash -lc 'echo -n $HOME'")
        remote_home = stdout.read().decode().strip() or "/home"
        remote_json_path = f"{remote_home}/system_logs/system_logs_ai.json"

        # Try to run existing script first
        try:
            stdin, stdout, stderr = ssh.exec_command("bash -lc 'bash ~/ai/collect_logs_ai.sh'")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                raise RuntimeError("Remote script not found or failed")
        except Exception:
            # Fallback: upload local script and execute
            try:
                sftp = ssh.open_sftp()
                remote_tmp = "/tmp/collect_logs_ai.sh"
                with open("collect_logs_ai.sh", "rb") as f:
                    sftp.putfo(f, remote_tmp)
                sftp.chmod(remote_tmp, 0o755)
                sftp.close()

                stdin, stdout, stderr = ssh.exec_command(f"bash -lc '{remote_tmp}'")
                _ = stdout.channel.recv_exit_status()
            except Exception as e2:
                ssh.close()
                return JSONResponse({"error": f"Failed to run logs collector on remote: {e2}"}, status_code=500)

        # Fetch the structured JSON back
        sftp = ssh.open_sftp()
        local_tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            sftp.get(remote_json_path, local_tmp.name)
        finally:
            sftp.close()
            ssh.close()

        with open(local_tmp.name) as f:
            logs = json.load(f)

        # Extract basic metrics if available
        metrics = logs.get("suspicious_events", {})

        # Ask Gemini for a structured report
        prompt = """
        You are a cybersecurity analyst. Analyze the provided system logs and indicators.
        Respond ONLY in valid JSON with the following structure:
        {
          "attack_detected": "short summary of the attack or 'No attack detected'",
          "evidence": ["list of evidence points"],
          "recommendations": ["list of security fixes and next steps"]
        }
        """
        response = model.generate_content([prompt, json.dumps(logs)])

        try:
            report = json.loads(response.text)
        except Exception:
            report = {
                "attack_detected": "Parsing error",
                "evidence": [getattr(response, "text", "No text")],
                "recommendations": []
            }

        report["metrics"] = {
            "failed_ssh_logins": metrics.get("failed_ssh_logins", 0),
            "root_logins": metrics.get("root_logins", 0),
            "unusual_processes": metrics.get("unusual_processes_as_nobody", 0)
        }

        return JSONResponse(report)

    except Exception as e:
        return JSONResponse({"error": f"Unexpected error: {str(e)}"}, status_code=500)

class VMConfigUpdate(BaseModel):  # schema for POST /configure-vm
    vm_id: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None

@app.post("/configure-vm")  # allow manual VM IP/credentials and force reconnect
def configure_vm(cfg: VMConfigUpdate):
    if cfg.vm_id not in VM_CONFIG:
        return JSONResponse({"error": "Unknown vm_id"}, status_code=400)
    
    VM_CONFIG[cfg.vm_id]["host"] = cfg.host
    VM_CONFIG[cfg.vm_id]["port"] = cfg.port
    VM_CONFIG[cfg.vm_id]["username"] = cfg.username
    VM_CONFIG[cfg.vm_id]["password"] = cfg.password or ""
    
    # Close existing SSH session (if any) so next websocket connect will use new config
    try:
        ch = ssh_channels.pop(cfg.vm_id, None)
        if ch:
            ch.close()
    except Exception:
        pass
    try:
        cl = ssh_clients.pop(cfg.vm_id, None)
        if cl:
            cl.close()
    except Exception:
        pass
    
    return JSONResponse({"status": "updated", "vm_id": cfg.vm_id})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
