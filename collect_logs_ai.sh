#!/bin/bash
# Filename: collect_logs_ai.sh
# Purpose: Collect system logs in structured JSON for AI analysis

OUTPUT_DIR=~/system_logs
JSON_FILE="$OUTPUT_DIR/system_logs_ai.json"

mkdir -p "$OUTPUT_DIR"

echo "[INFO] Collecting logs..."

# Collect logs as arrays of lines
jqify() {
  # Convert file into JSON array of strings
  sed 's/"/\\"/g' "$1" | sed 's/^/"/; s/$/"/; s/$/,/' | sed '$s/,$//' 
}

# Suspicious events
FAILED_SSH=$(grep "Failed password" /var/log/auth.log | wc -l)
ROOT_LOGINS=$(grep "Accepted password for root" /var/log/auth.log | wc -l)
UNUSUAL_PROCESSES=$(ps -eo user,comm | grep nobody | wc -l)

# Start JSON
{
echo "{"

# 1. Auth log
echo "\"auth_log\": ["
jqify /var/log/auth.log
echo "],"

# 2. Syslog
echo "\"syslog\": ["
jqify /var/log/syslog
echo "],"

# 3. Kernel log
echo "\"kern_log\": ["
jqify /var/log/kern.log
echo "],"

# 4. Last login
echo "\"last_login\": ["
last -i | sed 's/"/\\"/g' | sed 's/^/"/; s/$/"/; s/$/,/' | sed '$s/,$//'
echo "],"

# 5. Failed login
echo "\"failed_login\": ["
sudo lastb | sed 's/"/\\"/g' | sed 's/^/"/; s/$/"/; s/$/,/' | sed '$s/,$//'
echo "],"

# 6. Processes
echo "\"processes\": ["
ps aux --sort=-%mem | sed 's/"/\\"/g' | sed 's/^/"/; s/$/"/; s/$/,/' | sed '$s/,$//'
echo "],"

# 7. Network connections
echo "\"network_connections\": ["
sudo ss -tulnp | sed 's/"/\\"/g' | sed 's/^/"/; s/$/"/; s/$/,/' | sed '$s/,$//'
echo "],"

# 8. Suspicious events
echo "\"suspicious_events\": {"
echo "  \"failed_ssh_logins\": $FAILED_SSH,"
echo "  \"root_logins\": $ROOT_LOGINS,"
echo "  \"unusual_processes_as_nobody\": $UNUSUAL_PROCESSES"
echo "}"

# End JSON
echo "}"
} > "$JSON_FILE"

echo "[INFO] Structured logs saved to $JSON_FILE"
