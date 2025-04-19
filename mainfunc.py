import requests
from requests import get
import sys
from IPython.display import Javascript, display
from google.colab import output, files
from time import sleep
from json import load, dump
from os.path import exists
from os import makedirs
from IPython.display import clear_output

from rich import print



def LOG(*args, sep=''):
  check = False
  args = list(args)
  for i in range(len(args)):
    args[i] = str(args[i])
    if '\n' in args[i]: args[i] = args[i].replace('\n', ''); args.insert(0, '\n[bold green][ LOG ][/bold green] '); check = True; break
  if check == False: args.insert(0, '[bold green][ LOG ][/bold green] ')
  print(sep.join(map(str, args)))

def WARN(*args, sep=''):
  check = False
  args = list(args)
  for i in range(len(args)):
    args[i] = str(args[i])
    if '\n' in args[i]: args[i] = args[i].replace('\n', ''); args.insert(0, '\n[bold light_goldenrod1][ WARN ][/bold light_goldenrod1] '); check = True; break
  if check == False: args.insert(0, '[bold light_goldenrod1][ WARN ][/bold light_goldenrod1] ')
  print(sep.join(map(str, args)))
#---------------------------------------------------------------------------------- Print Box ------------------------------------------------------------------------------------------#
def print_msg_box(msg, indent=1, width=None, title=None):
    lines = msg.split('\n')
    space = " " * indent
    if not width:
        width = max(map(len, lines))
    border_color = "[bold gold3]"
    title_color = "[bold cyan]"
    text_color = "[bold white]"
    header_color = "[bold light_goldenrod1]"
    reset_color = "[/]"
    box = f'{border_color}+{"-" * (width + indent * 2)}+{reset_color}\n'  # upper_border
    if title:
        box += f'{border_color}|{space}{title_color}{title:<{width}}{reset_color}{space}{border_color}|\n'  # title
        box += f'{border_color}|{space}{"-" * len(title):<{width}}{space}{border_color}|\n'  # underscore
    box += ''.join([f'{border_color}|{space}{text_color}{line:<{width}}{reset_color}{space}{border_color}|\n' for line in lines])
    box += f'{border_color}+{"-" * (width + indent * 2)}+{reset_color}'  # lower_border
    print(box)
#------------------------------------------------------------------------------------------------------------------------------------#
log = "echo -e '\e[92m[ LOG ]\e[0m'" # Log function for bash
def ERROR(*args, sep=''):
  %cd $drive_path
  clear_output()
  check = False
  args = list(args)
  for i in range(len(args)):
    args[i] = str(args[i])
    if '\n' in args[i]: args[i] = args[i].replace('\n', ''); args.insert(0, '\n[ ERROR ] '); check = True; break
  if check == False: args.insert(0, '[ ERROR ] ')
  raise Exception(sep.join(map(str, args)))
def INFO(*args, sep=''):
  check = False
  args = list(args)
  for i in range(len(args)):
    args[i] = str(args[i])
    if '\n' in args[i]: args[i] = args[i].replace('\n', ''); args.insert(0, '\n[bold blue][ INFO ][/bold blue] '); check = True; break
  if check == False: args.insert(0, '[bold blue][ INFO ][/bold blue] ')
  print(sep.join(map(str, args)))
def MKDIR(path):
  try:
    makedirs(path, exist_ok = True)
    LOG(f'Directory {path} created')
  except: ERROR(f'Directory {path} already existed')
def DOWNLOAD_FILE(url, path, file_name, force = False,headers=None):
  # Check gate
  if not force:
    if exists( path+ '/' + file_name): LOG(f'File {file_name} already existed')
    else:
      # Download file into file_name thourgh url
      r = GET(url,headers=headers)
      LOG('\nDownloading ' + file_name)
      with open(path + '/' + file_name, 'wb') as f:
        f.write(r.content)
  else:
    r = GET(url,headers=headers)
    LOG('\nDownloading ' + file_name)
    with open(path + '/' + file_name, 'wb') as f:
      f.write(r.content)
def GET(url,headers=None):
  r = get(url,headers=headers)
  # Check gate
  if r.status_code == 200:
    return r
  else: ERROR('Error '+ str(r.status_code) + "! Most likely you entered an unsupported version. Try running the code again if you think that shouldn't have happened.")

def COLABCONFIG(server_name):
  return f"{drive_path}/{server_name}/colabconfig.txt"
def COLABCONFIG_LOAD(server_name):
  if exists(COLABCONFIG(server_name)): return load(open(COLABCONFIG(server_name)))
  else: ERROR('Please checking whether you deleted your colabconfig file or not.')

def SERVER_IN_USE(server_name):
  if exists(f'{drive_path}/{server_name}') and server_name != '': return server_name
  else:
    serverconfig = load(open(SERVERCONFIG))
    if serverconfig['server_in_use'] != '': return serverconfig['server_in_use']
    else: ERROR('Please create a minecraft server first!')
# The jar file name
def JAR_LIST_RUN(server_version):
  return {'generic': 'server.jar', 'vanilla':'server.jar','snapshot': 'server.jar',   # NORMAL
          'purpur' : 'server.jar', 'paper': 'server.jar', 'velocity' : 'server.jar', 'folia': 'server.jar',  # PLUGINS
          'fabric' : 'server.jar',  # forge doesn't include in this category           # MODS
          'arclight' : 'server.jar', 'mohist': 'server.jar', 'banner': 'server.jar'} # HYBRID
def JavaUrl(build, version):
  if build == "Corretto":
    return f"https://corretto.aws/downloads/latest/amazon-corretto-{version}-x64-linux-jdk.deb"
  elif build == "Azul Zulu":
    return [i["download_url"] for i in GET("https://api.azul.com/metadata/v1/zulu/packages?availability_types=ca&release_status=both&page_size=1000&include_fields=os,arch,java_package_type,archive_type&page=1&azul_com=true").json() if i["archive_type"] == "deb" and i["java_package_type"] == "jre" and "amd64" in i["name"] and not "fx" in i["name"] and i["java_version"] == list(map(int, version.split('.')))]



