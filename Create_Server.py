# Cardboard
def get_mod_versions(mod):
    """
    Obtains the information of the available versions of a mod from Modrinth.
    """
    url = f"https://api.modrinth.com/v2/project/{mod}/version"
    response = requests.get(url)
    if response.ok:
        # Se retorna la lista completa de diccionarios de versión
        return response.json()
    else:
        ERROR(f"Error fetching {mod} versions from Modrinth.")

def get_mod_download_url(mod, version):
    """
    Searchs in the API for the download URL of a specific version of a mod.
    """
    url = f"https://api.modrinth.com/v2/project/{mod}/version"
    response = requests.get(url)
    if response.ok:
        data = response.json()
        for v in data:
            # version_number or name to search for the game version
            ver_num = v.get("version_number") or v.get("name")
            if ver_num == version:
                for file in v.get("files", []):
                    if file.get("filename", "").endswith(".jar"):
                        return file.get("url")
        ERROR(f"Specified {mod} version not found")
    else:
        ERROR(f"Error fetching {mod} download URL from Modrinth")

def install_fabric_and_cardboard(version):
    # Installs Fabric
    fabric_versions = SERVERSJAR("GetVersions", server_type="Cardboard")
    if not fabric_versions:
        ERROR("No Fabric versions found.")

    if version not in fabric_versions:
        ERROR(f"Fabric version {version} not found.")

    LOG(f"Installing Fabric {version}...")
    fabric_url = SERVERSJAR("GetDownloadUrl", server_type="fabric", version=version)

    os.makedirs(f'{drive_path}/{server_name}', exist_ok=True)
    fabric_jar_path = f'{drive_path}/{server_name}/fabric-server-{version}.jar'

    if not exists(fabric_jar_path):
        LOG(f"Downloading Fabric {version}...")
        os.system(f'wget -O "{fabric_jar_path}" "{fabric_url}"')
        os.rename(fabric_jar_path, f'{drive_path}/{server_name}/fabric-installer.jar')

    LOG(f"Fabric {version} installed successfully.")

    def install_required_mods(mod_id, mod_name, fabric_version):
        LOG(f"Installing {mod_name}")
        versions = get_mod_versions(mod_id)
        if not versions:
            ERROR(f"No versions found for {mod_name}.")

        # Searchs for the latest mod versions that matches the "game_versions"
        compatible_version = next(
            (v for v in versions if fabric_version in v.get("game_versions", [])),
            None
        )

        if not compatible_version:
            ERROR(f"No compatible {mod_name} version found for Fabric {fabric_version}.")

        mod_version_number = compatible_version.get("version_number") or compatible_version.get("name")
        LOG(f"Compatible {mod_name} version found: {mod_version_number}")
        mod_url = get_mod_download_url(mod_id, mod_version_number)
        mod_jar_path = f'{drive_path}/{server_name}/{mod_name}-{mod_version_number}.jar'

        if not exists(mod_jar_path):
            LOG(f"Downloading {mod_name} {mod_version_number}...")
            os.system(f'wget -O "{mod_jar_path}" "{mod_url}"')
            os.makedirs(f"{drive_path}/{server_name}/mods", exist_ok=True)
            os.rename(mod_jar_path, f"{drive_path}/{server_name}/mods/{mod_name}-{mod_version_number}.jar")

        LOG(f"{mod_name} {mod_version_number} installed successfully.")

    # Installs the two required mods to work with Cardboard and Carboard.
    install_required_mods("MLYQ9VGP", "Cardboard API", version)
    install_required_mods("P7dR8mSH", "Fabric API", version)
    install_required_mods("SVKv1SZo", "iCommon API", version)

#---------------------------------------------------------------------------------- SNALL FUNCTION ------------------------------------------------------------------------------------#
def SERVERSJAR(command, server_type=None, version=None):
    # Get the download URL (jar) AND return the detailed versions for each software (all)
    if command == "GetVersions":
        if server_type is None:
            ERROR("No server type specified.")
        Server_Jars_All = {
            'paper': 'https://api.papermc.io/v2/projects/paper',
            'velocity': 'https://api.papermc.io/v2/projects/velocity',
            'purpur': 'https://api.purpurmc.org/v2/purpur',
            'mohist': 'https://mohistmc.com/api/v2/projects/mohist',
            'banner': 'https://mohistmc.com/api/v2/projects/banner',
            'folia': 'https://api.papermc.io/v2/projects/folia'
        }
        if server_type in ['vanilla', 'snapshot']:
            rJSON = get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
            if server_type == 'vanilla':
                server_type = 'release'
            if version != 'vanilla - latest_version':
                server_version = [hit["id"] for hit in rJSON["versions"] if hit["type"] == server_type]
            else:
                return rJSON['latest']['release']

        elif server_type in ['paper','velocity','purpur','mohist','banner','folia']:
            rJSON = get(Server_Jars_All[server_type]).json()
            server_version = [hit for hit in rJSON["versions"]]

        elif server_type == 'fabric':
            rJSON = get('https://meta.fabricmc.net/v2/versions/game').json()
            server_version = [hit['version'] for hit in rJSON if hit['stable'] == True]
        elif server_type == "neoforge":
            rJSON = get("https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge").json()
            server_version = [f"1.{hit[:4]} - {hit}" for hit in rJSON["versions"]]

        elif server_type == 'forge':
            from bs4 import BeautifulSoup
            rJSON = get('https://files.minecraftforge.net/net/minecraftforge/forge/index.html')
            soup = BeautifulSoup(rJSON.content, "html.parser")
            server_version = [tag.text for tag in soup.find_all('a') if '.' in tag.text and '\n' not in tag.text]
        elif server_type == "bedrock":
            HEADERS = {"User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 12871.102.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.141 Safari/537.36"}
            URL = "https://www.minecraft.net/en-us/download/server/bedrock/"
            BACKUP_URL = "https://raw.githubusercontent.com/ghwns9652/Minecraft-Bedrock-Server-Updater/main/backup_download_link.txt"
            try:
                from bs4 import BeautifulSoup
                page = requests.get(URL, headers=HEADERS, timeout=5)
                soup = BeautifulSoup(page.content, "html.parser")
                a_tag_res = [a['href'] for a in soup.find_all('a', attrs={"aria-label":"Download Minecraft Dedicated Server software for Ubuntu (Linux)"})]
                download_link = a_tag_res[0]
            except requests.exceptions.Timeout:
                LOG("timeout raised, recovering")
                page = requests.get(BACKUP_URL, headers=HEADERS, timeout=5)
                download_link = page.text
            server_version = download_link.split('bedrock-server-')[1].split(".zip")[0]
        elif server_type == "arclight":
            LOG('Before going deeper, please check out https://github.com/IzzelAliz/Arclight')
            rJSON = get('https://files.hypoglycemia.icu/v1/files/arclight/minecraft').json()['files']
            server_version  = [hit['name'] for hit in rJSON]
        elif server_type == "Crucible":
            confirm = input("The only available version for Crucible is 1.7.10. Are you sure you want to continue? (y/n)")
            if confirm.lower() == "y":
                server_version = "1.7.10"
            else:
                os.system(f"rm {drive_path}/{server_name}")
                ERROR("Aborted")
        elif server_type == "Magma":
            server_version = ["1.12.2", "1.18.2", "1.19.3", "1.20.1"]
        elif server_type == "Ketting":
            confirm = input("The only available versions for Ketting is 1.20.x. Are you sure you want to continue? (y/n)")
            if confirm.lower() == "y":
                server_version = "1.20"
            else:
                ERROR("Aborted. Server folder deleted.")
        elif server_type == "Cardboard":
            rJSON = requests.get("https://api.modrinth.com/v2/project/MLYQ9VGP/version").json()
            server_version = []
            for v in rJSON:
                if v['game_versions'][0] not in server_version:
                    server_version.append(v['game_versions'][0])
        else:
            ERROR('Wrong server type.')
        return server_version

    elif command == "GetDownloadUrl":
        if version is None:
            ERROR("No version specified.")
        if server_type in ['vanilla', 'snapshot']:
            rJSON = get('https://launchermeta.mojang.com/mc/game/version_manifest.json').json()
            if server_type == 'vanilla':
                server_type = 'release'
            for hit in rJSON["versions"]:
                if hit["type"] == server_type and hit['id'] == version:
                    return get(hit['url']).json()["downloads"]['server']['url']

        elif server_type in ['paper','velocity','folia']:
            build = get(f'https://api.papermc.io/v2/projects/{server_type}/versions/{version}').json()["builds"][-1]
            jar_name = get(f'https://api.papermc.io/v2/projects/{server_type}/versions/{version}/builds/{build}').json()["downloads"]["application"]["name"]
            return f'https://api.papermc.io/v2/projects/{server_type}/versions/{version}/builds/{build}/downloads/{jar_name}'

        elif server_type == 'purpur':
            build = get(f'https://api.purpurmc.org/v2/purpur/{version}').json()["builds"]["latest"]
            return f'https://api.purpurmc.org/v2/purpur/{version}/{build}/download'

        elif server_type in ['mohist', 'banner']:
            return get(f'https://mohistmc.com/api/v2/projects/{server_type}/{version}/builds').json()["builds"][-1]["url"]

        elif server_type == 'fabric':
            installerVersion = get('https://meta.fabricmc.net/v2/versions/installer').json()[0]["version"]
            fabricVersion = get(f'https://meta.fabricmc.net/v2/versions/loader/{version}').json()[0]["loader"]["version"]
            return "https://meta.fabricmc.net/v2/versions/loader/" + version + "/" + fabricVersion + "/" + installerVersion + "/server/jar"

        elif server_type == 'forge':
            from bs4 import BeautifulSoup
            rJSON = get(f'https://files.minecraftforge.net/net/minecraftforge/forge/index_{version}.html')
            soup = BeautifulSoup(rJSON.content, "html.parser")
            tag = soup.find('a', title="Installer")
            tag = str(tag)
            tag = tag[tag.find('"') + 1 :]
            link = tag[:tag.find('"')]
            link = link[link.find('=') + 1:]
            link = link[link.find('=') + 1:]
            return link

        elif server_type == "neoforge":
            version = version.split(" - ")[-1]
            return f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{version}/neoforge-{version}-installer.jar"
        elif server_type == 'arclight':
            rJSON = get(f'https://files.hypoglycemia.icu/v1/files/arclight/minecraft/{version}/loaders').json()
            LOG('Available type: ')
            print([hit['name'] for hit in rJSON['files']])
            build = input(' Type: ')
            choice = input('Stable(st) or Snapshot(sn): ')
            if 'sn' in choice.lower():
                choice = 'latest-snapshot'
            else:
                choice = "latest-stable"
            return f'https://files.hypoglycemia.icu/v1/files/arclight/minecraft/{version}/loaders/{build}/{choice}'

        elif server_type == 'bedrock':
            HEADERS = {"User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 12871.102.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.141 Safari/537.36"}
            URL = "https://www.minecraft.net/en-us/download/server/bedrock/"
            BACKUP_URL = "https://raw.githubusercontent.com/ghwns9652/Minecraft-Bedrock-Server-Updater/main/backup_download_link.txt"
            try:
                from bs4 import BeautifulSoup
                page = requests.get(URL, headers=HEADERS, timeout=5)
                soup = BeautifulSoup(page.content, "html.parser")
                a_tag_res = [a['href'] for a in soup.find_all('a', attrs={"aria-label":"Download Minecraft Dedicated Server software for Ubuntu (Linux)"})]
                download_link = a_tag_res[0]
            except requests.exceptions.Timeout:
                LOG("timeout raised, recovering")
                page = requests.get(BACKUP_URL, headers=HEADERS, timeout=5)
                download_link = page.text
            return download_link

        elif server_type == "Magma":
            if version == "1.12.2":
                return "https://github.com/magmamaintained/Magma-1.12.2/releases/download/b4c01d2/Magma-1.12.2-b4c01d2-server.jar"
            elif version == "1.18.2":
                return "https://github.com/magmamaintained/Magma-1.18.2/releases/download/9938562/magma-1.18.2-40.2.21-9938562-server.jar"
            elif version == "1.19.3":
                return "https://github.com/magmamaintained/Magma-1.19.3/releases/download/0efc583/magma-1.19.3-44.1.23-0efc583-server.jar"
            elif version == "1.20.1":
                return "https://github.com/magmamaintained/Magma-1.20.1/releases/download/b4bdd80/magma-1.20.1-47.2.20-b4bdd80-server.jar"
            else:
                ERROR("The selected version is not on the list of available versions.")

        elif server_type == "Crucible":
            return "https://github.com/CrucibleMC/Crucible/releases/download/v5.4/Crucible-1.7.10-5.4.jar"

        elif server_type == "Ketting":
            return "https://github.com/kettingpowered/kettinglauncher/releases/download/v1.5.1/kettinglauncher-1.5.1-sources.jar"

        elif server_type == "Cardboard":
            # Se usa la función auxiliar para obtener el enlace de descarga dinámicamente
            return install_fabric_and_cardboard(version)
        else:
            ERROR('Wrong server type.')
    elif command == "GetServerTypes":
        return ['vanilla','snapshot','paper','purpur','mohist', "arclight",'velocity', 'banner', 'fabric',"folia", 'forge',"neoforge", 'bedrock', 'Crucible', 'Magma', 'Ketting', 'Cardboard']
    else:
        ERROR("Not a valid command.")
