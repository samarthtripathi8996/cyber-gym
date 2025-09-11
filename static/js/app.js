function updateTime() {
            const now = new Date();
            document.getElementById('current-time').textContent = 
                now.toLocaleString('en-US', { 
                    timeZone: 'UTC',
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false
                }) + ' UTC';
        }
        updateTime();
        setInterval(updateTime, 1000);

        class VMMonitor {
            constructor(vmId) {
                this.vmId = vmId;
                this.terminal = null;
                this.terminalWs = null;
                this.statsWs = null;
                this.init();
            }
            
            init() {
                this.setupTerminal();
                this.connectTerminal();
                this.connectStats();
            }
            
            setupTerminal() {
                this.terminal = new Terminal({
                    cursorBlink: true,
                    theme: {
                        background: '#0c0c0c',
                        foreground: '#e6edf3',
                        cursor: '#1f6feb',
                        black: '#484f58',
                        red: '#ff7b72',
                        green: '#7ee787',
                        yellow: '#ffa657',
                        blue: '#79c0ff',
                        magenta: '#d2a8ff',
                        cyan: '#a5f3fc',
                        white: '#f0f6fc',
                        brightBlack: '#6e7681',
                        brightRed: '#ffa198',
                        brightGreen: '#56d364',
                        brightYellow: '#ffdf5d',
                        brightBlue: '#1f6feb',
                        brightMagenta: '#bf8cff',
                        brightCyan: '#56d4dd',
                        brightWhite: '#ffffff'
                    },
                    fontSize: 13,
                    fontFamily: 'JetBrains Mono, Menlo, Monaco, Consolas, monospace',
                    lineHeight: 1.4,
                    letterSpacing: 0,
                    scrollback: 2000,
                    allowTransparency: true,
                    minimumContrastRatio: 1
                });
                
                this.terminal.open(document.getElementById(`${this.vmId}-terminal`));
                this.fitTerminal();
                
                this.terminal.onData(data => {
                    if (this.terminalWs && this.terminalWs.readyState === WebSocket.OPEN) {
                        this.terminalWs.send(JSON.stringify({
                            type: 'terminal_input',
                            data: data
                        }));
                    }
                });
            }
            
            fitTerminal() {
                const container = this.terminal.element.parentElement;
                if (container) {
                    const containerWidth = container.clientWidth - 16;
                    const containerHeight = container.clientHeight - 16;
                    const cellWidth = 8;
                    const cellHeight = 18;
                    
                    const cols = Math.floor(containerWidth / cellWidth);
                    const rows = Math.floor(containerHeight / cellHeight);
                    
                    this.terminal.resize(Math.max(cols, 80), Math.max(rows, 24));
                }
            }
            
            connectTerminal() {
                const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${wsProtocol}//${window.location.host}/ws/terminal/${this.vmId}`;
                
                this.terminalWs = new WebSocket(wsUrl);
                
                this.terminalWs.onopen = () => {
                    this.updateStatus('connected');
                    this.terminal.write('\x1b[2J\x1b[H'); // Clear screen
                    this.terminal.write(`\x1b[32m[INFO]\x1b[0m Connected to ${this.vmId}\r\n`);
                    this.terminal.write(`\x1b[90m${new Date().toISOString()}\x1b[0m\r\n\r\n`);
                };
                
                this.terminalWs.onmessage = (event) => {
                    const message = JSON.parse(event.data);
                    if (message.type === 'terminal_output') {
                        this.terminal.write(message.data);
                    } else if (message.type === 'error') {
                        this.terminal.write(`\r\n\x1b[31m[ERROR]\x1b[0m ${message.message}\r\n`);
                    }
                };
                
                this.terminalWs.onclose = () => {
                    this.updateStatus('disconnected');
                    setTimeout(() => this.connectTerminal(), 3000);
                };
                
                this.terminalWs.onerror = () => {
                    this.updateStatus('error');
                };
            }
            
            connectStats() {
                const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${wsProtocol}//${window.location.host}/ws/stats/${this.vmId}`;
                
                this.statsWs = new WebSocket(wsUrl);
                
                this.statsWs.onmessage = (event) => {
                    const message = JSON.parse(event.data);
                    if (message.type === 'stats') {
                        this.updateStats(message.data);
                    }
                };
                
                this.statsWs.onclose = () => {
                    setTimeout(() => this.connectStats(), 5000);
                };
            }
            
            updateStatus(status) {
                const statusElement = document.getElementById(`${this.vmId}-status`);
                const dot = statusElement.querySelector('div');
                const text = statusElement.querySelector('span');
                
                switch(status) {
                    case 'connected':
                        dot.className = 'w-2 h-2 rounded-full bg-accent status-dot';
                        text.textContent = 'Online';
                        text.className = 'text-sm text-accent';
                        break;
                    case 'disconnected':
                        dot.className = 'w-2 h-2 rounded-full bg-warning';
                        text.textContent = 'Offline';
                        text.className = 'text-sm text-warning';
                        break;
                    case 'error':
                        dot.className = 'w-2 h-2 rounded-full bg-red-500';
                        text.textContent = 'Error';
                        text.className = 'text-sm text-red-400';
                        break;
                }
            }
            
            updateStats(stats) {
                this.updateMetric(`${this.vmId}-cpu`, stats.cpu, '%');
                this.updateMetric(`${this.vmId}-memory`, stats.memory, '%');
                this.updateMetric(`${this.vmId}-disk`, stats.disk, '%');
                this.updateMetric(`${this.vmId}-load`, stats.load, '', 2);
            }
            
            updateMetric(elementId, value, suffix, decimals = 0) {
                const element = document.getElementById(elementId);
                if (element) {
                    const displayValue = typeof value === 'number' 
                        ? value.toFixed(decimals) + suffix
                        : value + suffix;
                    element.textContent = displayValue;
                    
                    // Update color based on value
                    if (typeof value === 'number') {
                        element.className = this.getMetricColorClass(value, elementId.includes('load'));
                    }
                }
            }
            
            getMetricColorClass(value, isLoad = false) {
                const baseClass = 'text-2xl font-mono font-medium';
                if (isLoad) {
                    if (value > 2) return `${baseClass} metric-high`;
                    if (value > 1) return `${baseClass} metric-medium`;
                    return `${baseClass} text-white`;
                }
                
                if (value > 80) return `${baseClass} metric-high`;
                if (value > 60) return `${baseClass} metric-medium`;
                return `${baseClass} text-white`;
            }
        }

        // Initialize when DOM is ready
        document.addEventListener('DOMContentLoaded', function() {
            const vm1Monitor = new VMMonitor('vm1');
            const vm2Monitor = new VMMonitor('vm2');
            
            // Handle window resize
            let resizeTimeout;
            window.addEventListener('resize', () => {
                clearTimeout(resizeTimeout);
                resizeTimeout = setTimeout(() => {
                    vm1Monitor.fitTerminal();
                    vm2Monitor.fitTerminal();
                }, 100);
            });
        });