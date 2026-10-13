import os, paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "10.12.1.124",
    username="user",
    password=os.environ["WS_PASS"],
    timeout=20,
    allow_agent=False,
    look_for_keys=False,
)
cmd = (
    'wsl -e bash -lc '
    '"nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv; '
    'echo ---; curl -sf --max-time 5 http://127.0.0.1:11434/api/ps; echo"'
)
_i, o, e = c.exec_command(cmd, timeout=30)
print(o.read().decode())
err = e.read().decode()
if err.strip():
    print("STDERR", err[:500])
c.close()
