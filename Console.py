import requests
import os
from requests import get
from IPython.display import Javascript, display
from google.colab import output, files
from time import sleep
from json import load, dump
from os.path import exists
from os import makedirs
from IPython.display import clear_output
import shlex

def CONNECT_NGROK(port, type_, proxy):
  # Get serverconfig['ngrok_proxy'] : dict includes (authtoken, region)
  token = proxy['authtoken']
  os.system(f"ngrok authtoken {token}")
  region = proxy['region']
  conf.get_default().region = region
  url = ngrok.connect (port, type_)
  if type_ == 'tcp': return 'Your server address is ' + ((str(url).split('"')[1::2])[0]).replace('tcp://', '')
  else: return url
def CONFIG_ZROK(serverconfig):
  zrok_dir = "tunnel/zrok"
  if exists(shlex.quote(f"{drive_path}/{server_name}/{zrok_dir}/zrok")): # Check if zrok is installed
    os.system(f"chmod 777 {zrok_dir}/zrok")
    os.system(f"chmod +x {zrok_dir}/zrok")
    LOG("Checking zrok updates")
    v= os.system(f"tunnel/zrok/zrok version | grep v")
    v=v[-1].split()[0]
    lv=GET("https://api.github.com/repos/openziti/zrok/releases/latest").json()["tag_name"]
    if v < lv: #If zrok version is outdated
      LOG(f"You're running an outdated version of zrok. Version {v}, latest {lv}. Updating")
      zrok_url, zrok_name = [[i["browser_download_url"], i["name"]] for i in GET("https://api.github.com/repos/openziti/zrok/releases/latest").json()["assets"] if "linux_amd64" in i["browser_download_url"]][-1]
      DOWNLOAD_FILE(url=zrok_url, path = shlex.quote(f'{drive_path}/{server_name}/{zrok_dir}') , file_name = zrok_name)
      os.system(f'tar -xf {zrok_dir}/{zrok_name} -C {zrok_dir} > /dev/null && {log} "Installing zrok done" || echo "Installing zrok failed"')
      os.system(f"chmod 777 {zrok_dir}/zrok")
      os.system(f"chmod +x {zrok_dir}/zrok")
    status = os.system(f"./tunnel/zrok/zrok overview")
    if "unable to load environment" in str(status):
      LOG('Status: \n')
      os.system(f"./{zrok_dir}/zrok version")
      os.system(f"./{zrok_dir}/zrok status")
      token = serverconfig['zrok_proxy']['authtoken']
      os.system(f"./tunnel/zrok/zrok enable {token} --headless -d colab@colab >/dev/null && wait && {log} 'ZROK ENABLED WITH TOKEN' || echo 'ZROK NOT ENABLED.'")
    else: LOG("ZROK ALREADY ENABLED")
  else: # Zrok was not found. Installation process.
    zrok_dir = "tunnel/zrok" ; zrok_tar = 'zrok_0.4.32_linux_amd64.tar.gz';
    os.system(f"mkdir {shlex.quote(f'{drive_path}/{server_name}/{zrok_dir}')}")
    zrok_url, zrok_name = [[i["browser_download_url"], i["name"]] for i in GET("https://api.github.com/repos/openziti/zrok/releases/latest").json()["assets"] if "linux_amd64" in i["browser_download_url"]][-1]
    DOWNLOAD_FILE(url=zrok_url, path = shlex.quote(f'{drive_path}/{server_name}/{zrok_dir}') , file_name = zrok_name)
    os.system(f'tar -xf {zrok_dir}/{zrok_name} -C {zrok_dir} > /dev/null && {log} "Installing zrok done" || echo "Installing zrok failed"')
    os.system(f"chmod 777 {zrok_dir}/zrok")
    os.system(f"chmod +x {zrok_dir}/zrok")
    LOG('Status: \n')
    if exists(shlex.quote(f'{zrok_dir}/zrok')):
      os.system(f"./{zrok_dir}/zrok version")
      os.system(f"./{zrok_dir}/zrok status")
      LOG("Access to https://api.zrok.io to get fully management. \nDisabling zrok before enable. Don't care too much about this.\n")
      token = serverconfig['zrok_proxy']['authtoken']
      os.system(f'./{zrok_dir}/zrok enable {token} --headless -d colab@colab >/dev/null && wait && {log} "ZROK ENABLED WITH TOKEN" || echo "ZROK NOT ENABLED."')
def CONFIG_PLAYIT(serverconfig):
    # Download playit
    os.system('command -v playit || curl -SsL https://playit-cloud.github.io/ppa/key.gpg | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/playit.gpg > /content/drive/MyDrive/minecraft/logs/playitinstall.txt  2>&1 && echo "deb [signed-by=/etc/apt/trusted.gpg.d/playit.gpg] https://playit-cloud.github.io/ppa/data ./" | sudo tee /etc/apt/sources.list.d/playit-cloud.list >> /content/drive/MyDrive/minecraft/logs/playitinstall.txt  2>&1 && sudo apt -qq update >> /content/drive/MyDrive/minecraft/logs/playitinstall.txt  2>&1 && sudo apt install playit >> /content/drive/MyDrive/minecraft/logs/playitinstall.txt  2>&1 && echo "Playit.gg installed" >> /content/drive/MyDrive/minecraft/logs/playitinstall.txt  2>&1 || echo "Failed to install playit" >> /content/drive/MyDrive/minecraft/logs/playitinstall.txt  2>&1')
    secretfile= os.system("playit secret-path")
    secretfile = secretfile[0]
    if "playit_proxy" not in serverconfig:
      serverconfig["playit_proxy"] = {"secretkey": ""}
    if serverconfig["playit_proxy"]["secretkey"] == "":
      LOG("No token in settings. Running playit setup")
      os.system("printf '\e[36m[ PLAYIT ]\e[0m' && playit setup")
      sleep(5)
      LOG("Setup complete. Moving key to settings file")
      secretfile= os.system("playit secret-path")
      secretfile = secretfile[0]
      with open(secretfile, "r") as f:
        file = tload(f)
      secretkey=file["secret_key"]
      serverconfig['playit_proxy']['secretkey'] = secretkey
      dump(serverconfig, open(SERVERCONFIG, 'w'))
      LOG("Done.")
    elif serverconfig["playit_proxy"]["secretkey"] != "":
      LOG("Logging in with playit token.")
      config={}
      config['secret_key'] = serverconfig["playit_proxy"]["secretkey"]
      with open('/etc/playit/playit.toml', 'w') as f:
        tdump(config, f)
      sleep(5)
      status = os.system("playit tunnels list")
      os.system("playit secret-path")
      if status[0] == "{": LOG("Sucess.")
      elif status[0] == "Error: SecretFileLoadError": ERROR("[ PLAYIT ] Token not set. Please go to Software -> Playit Options > Setup/Reset Agent")
      elif status[0] == "Error: MalformedSecret": ERROR("[ PLAYIT ] Invalid token. Please go to Software -> Playit Options -> Setup/Reset Agent")
def CONFIG_TAILSCALE(serverconfig):
  status= os.system('command -v tailscale || echo "Error"')
  if "Error" in status:
    os.system("curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/bionic.gpg | sudo apt-key add - > /dev/null")
    os.system(f"curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/bionic.list | sudo tee /etc/apt/sources.list.d/tailscale.list > /dev/null")
    os.system(f'echo "Apt Update: \n" > /content/drive/MyDrive/minecraft/logs/tailscale-install.txt 2>&1 ; sudo apt-get update >> /content/drive/MyDrive/minecraft/logs/tailscale-install.txt 2>&1')
    os.system(f'echo "Apt Install Tailscale: \n" > /content/drive/MyDrive/minecraft/logs/tailscale-install.txt 2>&1 ;sudo apt-get install tailscale >> /content/drive/MyDrive/minecraft/logs/tailscale-install.txt 2>&1')


def ENABLE_TUNNEL(tunnel_service, serverconfig, server_type, log_path, geyser):
  if tunnel_service == "ngrok":
    if dynmap_check:
      dynmap_access=CONNECT_NGROK(type_= 'http', port= 8123, proxy = serverconfig['ngrok_proxy'])
    if server_type=="bedrock":
      print_msg_box(f"Bedrock: {CONNECT_NGROK(type_= 'udp', port= 19132, proxy = serverconfig['ngrok_proxy'])}", indent=4, width=60, title="Use the following ips to access your server")
    elif server_type!="bedrock" and geyser==True:
      print_msg_box(f"Bedrock: {CONNECT_NGROK(type_= 'udp', port= 19132, proxy = serverconfig['ngrok_proxy'])}\nJava: {CONNECT_NGROK(type_= 'tcp', port= 25565, proxy = serverconfig['ngrok_proxy'])}"+(f"\nDynmap: {dynmap_access}" if dynmap_check else ""), indent=4, width=60, title="Use the following ips to access your server")
    elif server_type!="bedrock":
      print_msg_box(f"Java: {CONNECT_NGROK(type_= 'tcp', port= 25565, proxy = serverconfig['ngrok_proxy'])}"+(f"\nDynmap: {dynmap_access}" if dynmap_check else ""), indent=4, width=60, title="Use the following ips to access your server")
  elif tunnel_service == "playit":
    CONFIG_PLAYIT(serverconfig)
    LOG("Starting Playit:")
    os.system("playit -s start &> /content/drive/MyDrive/minecraft/logs/playit.txt &")
    os.system(f'sleep 10 && cat /content/drive/MyDrive/minecraft/logs/playit.txt | strings | grep "got initial pong from tunnel server" >> /dev/null && {log} "Playit Connected" || echo "[ PLAYIT ] Error. Not connected. Please check logs."')
  elif tunnel_service == "zrok":
    CONFIG_ZROK(serverconfig)
    if dynmap_check:
      os.system("./tunnel/zrok/zrok share public localhost:8123 --headless &> $log_path/zrok2.txt &")
      #os.system(f'''sleep 20 && python -c "with open(f'/content/drive/MyDrive/minecraft/logs/zrok2.txt', 'r') as f: content = f.read(); content = content[content.find('}') + 1 : ]; from json import loads; json = loads(content[content.find('{'): content.find('}') + 1]); print('Your dynmap webserver: ', json['msg'][json['msg'].find('zrok') :])" &''')
    if server_type=="bedrock":
      os.system(f"./tunnel/zrok/zrok share private --backend-mode udpTunnel 127.0.0.1:19132 --headless &> $log_path/zrok3.txt &") # Bedrock Tunnel Start
      sleep(5)
    elif server_type!="bedrock" and geyser==True:
      os.system(f"./tunnel/zrok/zrok share private --backend-mode tcpTunnel 127.0.0.1:25565 --headless &> {log_path}/zrok.txt &") # Java tunnel start
      sleep(5)
      os.system(f"./tunnel/zrok/zrok share private --backend-mode udpTunnel 127.0.0.1:19132 --headless &> {log_path}/zrok3.txt &") # Bedrock (geyser) Tunnel Start
      sleep(5)
    elif server_type!="bedrock":
      os.system(f"./tunnel/zrok/zrok share private --backend-mode tcpTunnel 127.0.0.1:25565 --headless &> {log_path}/zrok.txt &") # Java tunnel start
      sleep(5)
    status = os.system("./tunnel/zrok/zrok status --secrets | grep 'Ziti Identity' | sed 's/\x1B\[[0-9;]\{1,\}[A-Za-z]//g'") # Get zrok user id and remove bash colors.
    zID=status[2].split()[2]
    data = os.system("./tunnel/zrok/zrok overview") # Get zrok data
    data=loads(data[0])
    for env in data["environments"]:
      environment = env.get("environment", {})
      if environment.get("zId") == zID:
        shares = env.get("shares", [])
        tunnels = []
        for share in shares: # Get running tunnels
          if share.get("backendMode") == "tcpTunnel":
              tunnels.append(f"Java: zrok access private {share.get('token')}")
          if share.get("backendMode") == "udpTunnel":
              tunnels.append(f"Bedrock: zrok access private {share.get('token')}")
          if share.get("backendMode") == "proxy":
              tunnels.append(f"Dynmap: {share.get('frontendEndpoint')}")
    if tunnels:
      print_msg_box("\n".join(tunnels), indent=4, width=60, title="Use the following commands/url to access your shares")
  elif tunnel_service == 'localtonet':
    localtonet_path = shlex.quote(f'{drive_path}/{server_name}/tunnel/localtonet/')
    if exists(f"{localtonet_path}/localtonet") == False:
      os.system("mkdir -p tunnel/localtonet")
      try:
        DOWNLOAD_FILE(url = 'https://localtonet.com/download/localtonet-linux-x64.zip', path = shlex.quote(f'{drive_path}/{server_name}/tunnel/localtonet'), file_name = 'localtonet-linux-x64.zip')
        LOG('Download successfull!')
      except:
        ERROR('LocalToNet could not be downloaded! Try again later or contact us.')
      sleep(4)
      os.system(f"sudo unzip -o tunnel/localtonet/localtonet-linux-x64.zip -d {localtonet_path} > /dev/null && echo 'Unzipped successfully!' || echo 'Failed to unzip.'")
      os.system("ls tunnel/localtonet")
      sleep(4)
    os.system("chmod 777 tunnel/localtonet/localtonet")
    os.system("chmod +x tunnel/localtonet/localtonet")
    LOG('Starting Tunnel: '); token = serverconfig['localtonet_proxy']['authtoken']
    os.system(f"./tunnel/localtonet/localtonet authtoken {token} &> {log_path}/localtonet.txt &")
    os.system("sleep 10 && cat /content/drive/MyDrive/minecraft/logs/localtonet.txt | strings | grep 'Connected' >> /dev/null && echo '[ LOG ] Locatonet tunnel started! Start the UDP_TCP connection on the dashboard: https://localtonet.com/tunnel/tcpudp.' || echo '[ ERROR ] Failed to start Locatonet tunnel.'")
  elif tunnel_service == "minekube-gate":
    gate_dir = "tunnel/minekube-gate"
    if exists(shlex.quote(f'{drive_path}/{server_name}/{gate_dir}/gate')) == False or exists(shlex.quote(f'{drive_path}/{server_name}/{gate_dir}/config.yml')) == False: # Check if gate is installed
      os.system(f"mkdir {shlex.quote(f'{drive_path}/{server_name}/{gate_dir}')}")
      try:
        DOWNLOAD_FILE(url = 'https://github.com/minekube/gate/releases/download/v0.41.2/gate_0.41.2_linux_amd64', path = shlex.quote(f'{drive_path}/{server_name}/{gate_dir}') , file_name = "gate")
        LOG('Download successfull!')
      except Exception as e:
        ERROR(f'minekube-gate could not be downloaded! Try again later or contact us. {e}')
      sleep(4)
      os.system(f"chmod 777 {gate_dir}/gate")
      os.system(f"chmod +x {gate_dir}/gate")
      DOWNLOAD_FILE(url = "https://raw.githubusercontent.com/N-aksif-N/MineColab_Improved/refs/heads/free-config/minekube-gate/config.yml", path = shlex.quote(f'{drive_path}/{server_name}/{gate_dir}' , file_name = "config.yml")) # temporal hasta que me habiliten el commit
      os.system(f"chmod 777 {gate_dir}/config.yml")
      LOG("Starting Gate")

    os.system(f"chmod 777 {gate_dir}/gate")
    os.system(f"chmod +x {gate_dir}/gate")
    os.system(f"chmod 777 {gate_dir}/config.yml")
    os.system(f"rm {log_path}/minekube-gate.txt")
    if "minekube-gate_proxy" not in serverconfig:
      serverconfig["minekube-gate_proxy"] = {"token": ""}
    if serverconfig["minekube-gate_proxy"]["token"] == "":
      LOG("No token in settings. Running without one.")
    elif serverconfig["minekube-gate_proxy"]["token"] != "":
      LOG("Logging in with minekube token.")
      config={}
      config['token'] = serverconfig["minekube-gate_proxy"]["token"]
      dump(config, open(shlex.quote(f"{drive_path}/{server_name}/{gate_dir}/connect.json"), 'w'))
      sleep(5)
    #!(cd tunnel/minekube-gate && ./gate -c $drive_path/$server_name/$gate_dir/config.yml > $log_path/minekube-gate.txt 2>&1 &) &
    #!bash -c "cd tunnel/minekube-gate && ./gate -c $drive_path/$server_name/$gate_dir/config.yml > $log_path/minekube-gate.txt 2>&1" &
    nouse= os.system(f'nohup bash -c "cd tunnel/minekube-gate && ./gate -c ./config.yml > {log_path}/minekube-gate.txt 2>&1" </dev/null &')
    sleep(5)
    status= os.system(f'cat /content/drive/MyDrive/minecraft/logs/minekube-gate.txt | strings | grep "connected" >> /dev/null && echo 1 || echo 2')
    if "2" in status:
      LOG("Failed to start Minekube gate. Please check logs.")
    elif "1" in status:
      LOG("Minekube gate connected sucessfully")
      endpoint = os.system('''grep "connecting to watch service..." /content/drive/MyDrive/minecraft/logs/minekube-gate.txt |  sed "s/\x1B\[[0-9;]\{1,\}[A-Za-z]//g"''')
      print_msg_box(loads(endpoint[0].split("\t")[-1])["endpoint"]+".play.minekube.net", indent=5, width=45, title="Use the following ip to access your shares")
    LOG("Playit startup")
    ENABLE_TUNNEL("playit", serverconfig, server_type, log_path, geyser) # Tunnel functions


  elif tunnel_service == 'argo':
    # Download argo
    if exists(shlex.quote(f'{drive_path}/{server_name}/tunnel/cloudflared-linux-amd64')) == False:
      DOWNLOAD_FILE(url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64', path = f'{drive_path}/{server_name}/tunnel', file_name= 'cloudflared-linux-amd64')
      os.system("chmod 777 tunnel/cloudflared-linux-amd64")
      os.system("chmod +x tunnel/cloudflared-linux-amd64")
    LOG('Starting: ');
    # Run tunnel
    os.system("./tunnel/cloudflared-linux-amd64 tunnel --url tcp://127.0.0.1:25565 &")
  elif tunnel_service == 'localxpose':
    ERROR('This tunnel is in develop... Try again with a new version.')
    if exists(shlex.quote(f'{drive_path}/{server_name}/tunnel/localxpose/loclx')) == False:
      os.system("mkdir tunnel/localxpose")
      DOWNLOAD_FILE(url = 'https://api.localxpose.io/api/v2/downloads/loclx-linux-amd64.zip', path = f'{drive_path}/{server_name}/tunnel', file_name= 'loclx-linux-amd64.zip')
      sleep(20)
      os.system("unzip -o tunnel/loclx-linux-amd64.zip")
      os.system("chmod 777 tunnel/localxpose/loclx")
      os.system("chmod +x tunnel/localxpose/loclx")
    LOG('Starting: ');
    token = serverconfig['localxpose_proxy']['authtoken']
    os.system(f"export ACCESS_TOKEN={token}")
    os.system(f"./tunnel/localxpose/loclx account login {token} &> {log_path}/localxpose.txt && loclx tunnel tcp --port 25565")
    os.system("echo 'LocalXpose tunnel started! Start the UDP_TCP connection on the dashboard: https://localxpose.io/dashboard'")
  elif tunnel_service == 'tailscale': # Tailscale tunnel service
    if "tailscale_proxy" not in serverconfig  or  serverconfig["tailscale_proxy"]["authtoken"] == "":
      ERROR("Tailscale auth token not specified. Please insert one on the 'Change Tunnel Token' cell")
    CONFIG_TAILSCALE(serverconfig)
    if "machine_info" not in serverconfig["tailscale_proxy"] or serverconfig["tailscale_proxy"]["machine_info"] == ""  :
      LOG("Tailscale Machine Info not found. Saving.")
      token = serverconfig['tailscale_proxy']['authtoken']
      os.system(f"nohup sudo tailscaled --tun=userspace-networking --socket=/run/tailscale/tailscaled.sock --port 41641 --state=/tmp/tailscaled/tailscaled.state  --statedir /tmp/tailscaled/ > {log_path}/tailscaled.txt 2>&1 &")
      os.system(f'tailscale up --authkey {token} --hostname colab && {log} "Tailscale Connected Successfully"')
      with open("/tmp/tailscaled/tailscaled.state", "r") as f:
        state=f.read()
      state=b64encode(state.encode("utf-8"))
      serverconfig['tailscale_proxy']['machine_info'] = state.decode("utf-8")
      dump(serverconfig, open(SERVERCONFIG, 'w'))
      LOG("Tailscale Machine Info saved to config file. Starting Minecraft Server")
      sleep(5)
    else:
      LOG("Tailscaled Machine Info found. Starting.")
      if not exists('/tmp/tailscaled'):
        os.system("mkdir /tmp/tailscaled/ ; touch  /tmp/tailscaled/tailscaled.state")
      with open("/tmp/tailscaled/tailscaled.state", "w") as f: f.write(b64decode(serverconfig["tailscale_proxy"]["machine_info"].encode("utf-8")).decode("utf-8"))
      token = serverconfig['tailscale_proxy']['authtoken']
      os.system(f"nohup sudo tailscaled --tun=userspace-networking --socket=/run/tailscale/tailscaled.sock --port 41641 --state=/tmp/tailscaled/tailscaled.state  --statedir /tmp/tailscaled/ > {log_path}/tailscaled.txt 2>&1 &")
      os.system(f'tailscale up --authkey {token} --hostname colab && {log} "Tailscale Connected Successfully"')
      sleep(5)
def RUNCOMMAND(server_name, serverconfig, version, _type, tunnel_service, hide = False):
    # Creating directories:
    #      tunnel - include all the tunnel sevice
    #      logs - include the tunnel logs
    os.system(f"mkdir '{drive_path}/logs'")
    os.system(f"mkdir {shlex.quote(f'{drive_path}/{server_name}/tunnel')}")
        # Forge need to open and run it before starting server. => Using command instead.
    if _type == 'forge':
      LOG("Running forge Installer. Please wait")
      os.system("java -jar forge-installer.jar --installServer >/dev/null")
    if _type == 'neoforge' and not exists(shlex.quote(f'{drive_path}/{server_name}/run.sh')):
      LOG("Running neoforge Installer. Please wait")
      os.system("java -jar neoforge-installer.jar --installServer >/dev/null")
    jar_name = ''
    if _type == 'forge':
      a = os.system("ls -1 | grep .jar")
      for i in a:
        if i.startswith("forge") and 'installer' not in i: jar_name = i
    elif _type == 'bedrock': jar_name = 'bedrock_server'
    elif _type == "neoforge": pass
    elif _type == 'crucible': jar_name = "Crucible-1.7.10-5.4.jar"
    elif _type == 'ketting': jar_name = "kettinglauncher-1.5.1-sources.jar"
    elif _type == 'Cardboard': jar_name = "fabric-installer.jar"
    elif _type == 'Magma': jar_name = "magma-installer.jar"
    else: jar_name = JAR_LIST_RUN(version)[_type]
    geyser=False
    a = listdir(f'{drive_path}/{server_name}')                     # Geyser plugin check
    if 'plugins' in a: a = listdir(shlex.quote(f'{drive_path}/{server_name}/plugins')) #
    elif 'mods' in a: a = listdir(shlex.quote(f'{drive_path}/{server_name}/mods'))     #
    for i in a:                                                           #
      if 'Geyser' in i or "geyser" in i:                                  #
        geyser=True                                                       #
    if _type!="bedrock" or geyser==True:
    # Install java acording to the version of server.jar
      INSTALL_JAVA(version, _type)
    # Get all the improving java arguments

    java_ver = os.system("java -version 2>&1 | awk -F[\"\.] -v OFS=. 'NR==1{print $2}'")
    args = " -Xms8G -Xmx8G"
    if _type == "paper" or _type == 'purpur' or _type == 'arclight':
      # Improving paper cilent (purpur is an alternative).
      # For more detailed: https://docs.papermc.io/paper/aikars-flags
      args += ' -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M -XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -Dusing.aikars.flags=https://mcflags.emc.gs -Daikars.new.flags=true'
    elif _type == "velocity":
      # Java argument for velocity server
      # For more detailed: https://docs.papermc.io/velocity/tuning
      args += ' -XX:+UseG1GC -XX:G1HeapRegionSize=4M -XX:+UnlockExperimentalVMOptions -XX:+ParallelRefProcEnabled -XX:+AlwaysPreTouch -XX:MaxInlineLevel=15'
    # GC_LOGGING
    if java_ver[0] == "1": args += ' -Xloggc:gc.log -verbose:gc -XX:+PrintGCDetails -XX:+PrintGCDateStamps -XX:+PrintGCTimeStamps -XX:+UseGCLogFileRotation -XX:NumberOfGCLogFiles=5 -XX:GCLogFileSize=1M'
    # else: args += '-Xlog:gc*:logs/gc.log:time,uptime:filecount=5,filesize=1M' # GC_Logging may not worked in java 11 and above

    # Set up run server cmd
    cmd = ''
    if exists(shlex.quote(f'{drive_path}/{server_name}/run.sh')) == True and _type != 'arclight':
      with open(shlex.quote(f'{drive_path}/{server_name}/run.sh'), 'r') as f: cmd = f.read()
    if cmd != '': cmd = cmd[cmd.find('java'): ].replace('@user_jvm_args.txt', args); cmd = cmd.replace('"$@"', 'nogui "$@"')
    else: cmd = f'java -server {args} -jar {jar_name} nogui'
    if _type == "bedrock":
      cmd=f"LD_LIBRARY_PATH=. {shlex.quote(f'{drive_path}/{server_name}/bedrock_server')}"
      bedrock_path = shlex.quote(f'{drive_path}/{server_name}/bedrock_server')
      os.system(f"sudo chmod 777 {bedrock_path}")

    if hide == True:
      if _type == 'forge' or _type == 'arclight' or _type == 'neoforge':
        # Make run.sh running
        if exists(shlex.quote(f'{drive_path}/{server_name}/eula.txt')): LOG('Run the server again to finished installing.')
      elif _type != 'mohist' and _type != 'banner':
        # Install needed file: eula.txt and so on.
        os.system(f"{cmd} > /dev/null")

    else:
      # Starting tunneling and run java file.
      LOG(f'Starting server ({tunnel_service})...')
      LOG('Stop server => /stop')
      log_path = shlex.quote(f'{drive_path}/logs')
      stype=_type
      ENABLE_TUNNEL(tunnel_service, serverconfig, stype, log_path, geyser) # Tunnel functions
      if _type == 'Magma': print("Magma's first installation might take up to 10 minutes. Be pacient.")
      LOG("Starting: ") ; sleep(5)
      os.system(cmd) # Server Startup

def GEYSER_CONFIG(server_name, colabconfig, _type):
    if 'Geysermc' in colabconfig:
      if 'fabric' in _type: path = shlex.quote(f'{drive_path}/{server_name}/config/Geyser-Fabric/config.yml')
      elif'paper' in _type or 'purpur' in _type: path = shlex.quote(f'{drive_path}/{server_name}/plugins/Geyser-Spigot/config.yml')
      else:
        LOG('Check out https://wiki.geysermc.org/floodgate/setup/ for installing Floodgate on servers behind the proxy'); LOG('Note for Fabric behind a Velocity proxy: You will need to configure FabricProxyLite to allow the Fabric server to receive data from Velocity.'); LOG('After done settings, copy the key.pem file in the proxy Floodgate config folder to all backend servers’ Floodgate config folder.'); LOG('DO NOT DISTRIBUTE THIS KEY TO ANYBODY! This key is what allows for Bedrock accounts to bypass the Java Edition authentication, and if anyone gets ahold of this, they can wreak havoc on your server.')
        path =  shlex.quote(f'{drive_path}/{server_name}/plugins/Geyser-Velocity/config.yml')
      # To get the requirement
      if exists(path):
        LOG('Config geyser plugin')
        # Configuring
        with open(path, 'r') as file:
          config = yaml.load(file);
        config['bedrock']['clone-remote-port'] = 'true';
        config['auth-type'] = 'floodgate';
        yaml.dump(config, sys.stdout)
        # Clear notification
        colabconfig.pop('Geysermc')
        dump(colabconfig, open(COLABCONFIG(server_name),'w'))
        LOG('Config done. Check out https://wiki.geysermc.org/geyser/playit-gg for setting tunnels \n - Verify whether connections from other networks are possible by running geyser connectiontest <ip>:<port> in the console.')
def SUPPORT_DYNMAP(colabconfig):
    global dynmap_check
    if exists(shlex.quote(f'{drive_path}/{server_name}/mods/dynmap-server.jar')) or exists(shlex.quote(f'{drive_path}/{server_name}/plugins/dynmap-server.jar')):
      if colabconfig['tunnel_service'] == 'ngrok':
        dynmap_check = True
      elif colabconfig['tunnel_service'] == 'playit': LOG('Access to https://playit.gg/support/add-dynmap-to-minecraft/ then head over to step 5. DO IT!!!!!!!')
      elif colabconfig['tunnel_service'] == 'localtonet': LOG('GUIDE: \n - Go to https://localtonet.com/tunnel/http \n - Select Process Type for your needs. (Random Sub Domain, Custom Sub Domain, Custom Domain)\n - Select your authtoken. Get it from https://localtonet.com/usertoken. And then select the Server you want your tunnel to run on.\n Enter the IP: 127.0.0.1 and Port: 8123 values that the tunnel will listen to\n - Create and Start your tunnel by pressing the Start Button from the list.')
      elif colabconfig['tunnel_service'] == 'zrok':
        dynmap_check = True

def INSTALL_JAVA(version, _type):
  if "java" not in colabconfig:
    colabconfig["java"] = {}
    colabconfig["java"] = {"CustomEnabled": "False", "version:": "", "build" : ""}
  def JAVAFUNCT(command, build="OpenJDK - Default", java_version=None):
    #Get the download URL (jar) AND return the detailed versions for each software (all)
    if command == "InstallJava":
      if java_version == None: ERROR("No version specified.")
      if build.lower() == 'openjdk - default' or build.lower() == "openjdk":
        os.system(f'''sudo apt-get -y install openjdk-{java_version}-jre-headless  &>/dev/null && echo "Openjdk {java_version} is working correctly, you are good to go." || "Openjdk {java_version} doesn't seem to be installed or isn't working."''')
        environ["JAVA_HOME"] = f"/usr/lib/jvm/java-{java_version}-openjdk-amd64"
        javadir=f"/usr/lib/jvm/java-{java_version}-openjdk-amd64/bin/java"
        os.system(f'update-alternatives --set java "{javadir}"')
      elif build.lower() == "amazon corretto":
        os.system("sudo apt-get install java-common --fix-broken &>/dev/null") # required to install corretto
        DOWNLOAD_FILE(url= JavaUrl("Corretto", version=java_version), path = "/tmp/", file_name="Corretto.deb", force=True)
        os.system('sudo apt install /tmp/Corretto.deb --fix-broken &>/dev/null && echo "Amazon Corretto installed correctly." || "Amazon Corretto failed to install."')
        environ["JAVA_HOME"] = f"/usr/lib/jvm/java-{java_version}-amazon-corretto"
        javadir=f"/usr/lib/jvm/java-{java_version}-amazon-corretto/bin/java"
        os.system(f'update-alternatives --set java "{javadir}"')

      elif build.lower() == "azul zulu":
        os.system("sudo apt-get install java-common --fix-broken &>/dev/null") # required to install corretto
        DOWNLOAD_FILE(url= JavaUrl("Azul Zulu", version=java_version), path = "/tmp/", file_name="zulu.deb", force=True)
        os.system('sudo apt install /tmp/zulu.deb --fix-broken &>/dev/null && echo "Azul Zulu installed correctly." || "Azul Zulu failed to install."')
        environ["JAVA_HOME"] = f"/usr/lib/jvm/zre-{java_version}-amd64"
        javadir=f"/usr/lib/jvm/zre-{java_version}-amd64/bin/java"
        os.system(f'update-alternatives --set java "{javadir}"')
      else: ERROR('Wrong java build.')

  if colabconfig["java"]["CustomEnabled"] == "True":
    LOG("Installing Custom Java. ")
    JAVAFUNCT("InstallJava", build=colabconfig["java"]["build"], java_version=colabconfig["java"]["version"])
  else:
    if _type == "velocity": # Velocity requires java 17 by default
      JAVAFUNCT("InstallJava", build="openjdk", java_version=17)
    elif _type == "neoforge": # Neoforge requires java 21 by default
      JAVAFUNCT("InstallJava", build="openjdk", java_version=21)

    elif _type == "mohist": # Mohist java requirements
      version_tuple = tuple(map(int, version.split('.')))

      if version_tuple in [(1, 7, 10), (1, 12, 2)]:
        JAVAFUNCT("InstallJava", build="openjdk", java_version=8)


      elif version_tuple == (1, 16, 5):
        JAVAFUNCT("InstallJava", build="openjdk", java_version=11)

      elif version_tuple >= (1,18,2):
        JAVAFUNCT("InstallJava", build="openjdk", java_version=17)
    else: # Paper, purpur, vanilla, spigot, bukkit, etc
      # Java 8 is required to run Minecraft versions 1.12 through 1.17. Java 17 is required to run Minecraft version 1.18 and up.
      version_tuple = tuple(map(int, version.split('.')))
      if version_tuple >= (1,20,5):
        JAVAFUNCT("InstallJava", build="openjdk", java_version=21)

      elif version_tuple >= (1,17):
        JAVAFUNCT("InstallJava", build="openjdk", java_version=17)

      else:
        JAVAFUNCT("InstallJava", build="openjdk", java_version=8)

def EXIT_NGROK():
    # Exiting ngrok tunnel helps improve the performance
    tunnels = ngrok.get_tunnels()
    for tunnel in tunnels: ngrok.disconnect(tunnel.public_url)
    ngrok.kill()
    LOG("Ngrok closed.")
