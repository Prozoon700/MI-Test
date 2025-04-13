class Download_:
  def __init__(self, choice, url, server_name, categories, versions, project_types, index):
    self.server_name = SERVER_IN_USE(server_name)
    if exists(f"{drive_path}/{self.server_name}/server.properties") == False: ERROR(' Running your minecraft server before editing properties')
    else:
      self.colabconfig = COLABCONFIG_LOAD(self.server_name)
      self.categories = categories
      self.versions = versions
      self.project_types = project_types
      self.index = index
      self.path = f'{drive_path}/{self.server_name}/{self.project_types}'
      self.choice = choice
      self.url = url
  def FACETS(self, software):
    # Get all the syntax
    categories = ''; index = ''; versions = ''
    if software == 'Modrinth':
      facets = "["
      if self.categories != 'none': facets += '["categories:' + self.categories + '"]';
      if facets != '[' and self.versions != 'none': facets += "," + '["versions:' + self.versions +'"]'
      elif self.versions != 'none': facets += '["versions:' + self.versions + '"]';
      if self.project_types == 'mods': project_types = 'mod'
      elif self.project_types == 'plugins': project_types = 'plugin'
      if facets != '[' and self.project_types != 'tmp': facets += "," + '["project_type:' + project_types + '"]'
      elif self.project_types != 'tmp': facets += '["project_type:' + project_types + '"]'
      facets += "]"; facetsInURL = "";
      if facets != "[]": facetsInURL += f'&facets={facets}'
      if self.index != "none": facetsInURL += f'&index={self.index}'
      return facetsInURL
    else:
      if self.categories != 'none':
        if self.categories == 'fabric': categories = 4 #Fabric
        elif self.categories == 'forge': categories = 1 #Forge
        elif self.categories == 'quilt': categories = 5 # quilt
        categories = f"&modLoaderType={categories}"
      if self.index != "none":
        if self.index == "relevance": index = 1 # Featured
        elif self.index == "downloads": index = 6 #TotalDownloads
        elif self.index == "follows": index = 2 #Popularity
        elif self.index == "newest": index = 11 #ReleasedDate
        elif self.index == "updated": index = 3 #LastUpdated
        index = f"&sortField={index}"
      if self.versions != "none": versions = f"&gameVersion={self.versions}"
      return categories + versions + index
  def SEARCH(self, search_name, software):
    project = {}
    LOG(f'\nSearching for the related of {search_name} ...\n')
    facetsInURL = self.FACETS(software)
    if software == "Modrinth":
      # Get syntax and get data
      rJSON = GET(f'https://api.modrinth.com/v2/search?query={search_name}{facetsInURL}').json()
      # Get the list of all relevant project
      for hit in rJSON['hits']:
        # Auditing if it for server or not.
        if hit['server_side'] == 'optional' or hit['server_side'] == 'required':
          # Get a full list title : description
          print(hit['slug'], " : ", hit['description'])
          project[hit['slug']] = hit['project_id']
    elif software == 'Curseforge':
      LOG(f"Curseforge doesn't have the exact searching key for cilent-side or server-side => you may get errors when running this {self.project_types}")
      # Because I haven't found any corresponded of project types in the search engine of Curseforge, I don't use it for searching.
      # The gameid of Minecraft is 432
      rJSON = GET(f"https://api.curse.tools/v1/cf/mods/search?gameId=432&searchFilter={search_name}{facetsInURL}").json()
      # Get the list of all relevant project
      for hit in rJSON["data"]:
        # Get a full list name: summary
        print(hit["name"], ' : ', hit["summary"])
        project[hit['name']] = str(hit["id"])
    # Checking whether your search name wrong or not. If yes => Get the name of project => Get project id
    project_names =''
    if project == {}: ERROR(f"\nSomething went wrong. Please check your search name.")
    else:
      LOG('\nType the project_name you want to download')
      project_names= input('Project_name: ')
      while project_names not in project:
        LOG('\nWrong project_names please type aigain. If you want to quit, type "None".')
        project_names= input('\nProject_name: ')
        if project_names == 'None': ERROR('Stopping...')
    return [project, project_names]
  def MODPACK(self, file_name, software):
    # settings up
    sleep(20)
    os.chdir(drive_path)
    server_name = self.server_name
    path_drive = file_name
    while path_drive.find('.') != -1: path_drive = path_drive[path_drive.find('.')+1:]
    path_drive = file_name[: file_name.find('.' + path_drive)]
    if exists(f'{drive_path}/{self.server_name}/tmp/{path_drive}') == False:
      MKDIR(f'{self.server_name}/tmp/{path_drive}')
      sleep(20)
    # Unzipping modpack
    os.system(f"unzip -q '{server_name}/tmp/{file_name}' -d '{server_name}/tmp/{path_drive}' > /dev/null &&echo 'Unzip done' || echo 'Failed to unzip'")
    sleep(20)
    # Copy the directory in orverrides and paste it (overrides includes the config files of the developer)
    try:
      for fln in listdir(f'{server_name}/tmp/{path_drive}/overrides'):
        os.system(f"mv -f '{server_name}/tmp/{path_drive}/overrides/{fln}' '{drive_path}/{server_name}' > /dev/null && echo 'Moving done' || echo 'Failed to move'")
        sleep(20)
    except: pass
    # Each page give the difference file json. The file json give full details.
    # (Modrinth: modrinth.index.json- Download link, Curseforge: manifest.json - fileID, projectID) for mods which is included in modpack.
    if software == 'Modrinth':
      with ZipFile(f'{server_name}/tmp/{file_name}') as myzip:
        manifest = loads(myzip.read('modrinth.index.json'))['files']
        for file_ in manifest:
          path_ = file_["path"].split("/")[0]; file_name_ = file_["path"].split("/")[1]
          DOWNLOAD_FILE(url = file_["downloads"][0], path = f'{drive_path}/{self.server_name}/{path_}', file_name = file_name_)
    else:
      with ZipFile(f'{server_name}/tmp/{file_name}') as myzip:
        manifest = loads(myzip.read('manifest.json'))['files']
        for file_ in manifest:
          project_id =  file_["projectID"]; fileID = str(file_["fileID"]); rJSON = GET(f'https://api.curse.tools/v1/cf/mods/{project_id}/files/{fileID}').json()["data"]
          DOWNLOAD_FILE(url = f'https://www.curseforge.com/api/v1/mods/{project_id}/files/{fileID}/download', path = f'{drive_path}/{self.server_name}/mods', file_name = rJSON["fileName"])
  def Install_(self, search_name, software):
    LOG(f'Acessing: {self.server_name}')
    if self.choice == 'url':
      url = self.url
      # Find file_name in download url
      filename = input('File name (optional) : ')
      if filename == '':
        filename = url[url.find("/") + 1:]
        while filename.find("/")!= -1: filename = filename[filename.find("/") + 1:]
      else:
        format = input('Format : ')
        if f".{format}" not in filename: filename += f".{format}"
      # Download file
      if ' ' in filename: filename = filename.replace(' ', '_')
      DOWNLOAD_FILE(url= url, path = self.path, file_name= filename)
      if self.project_types == 'tmp': self.MODPACK(file_name= filename, software= software); sleep(40)
      LOG('\nCompleted')
    elif self.choice == 'upload_manually':
      LOG('Upload here your FILE or ZIP.')
      WARN('Take into account that LARGE files (+ 5MB) are fastly uploaded through Drive.')
      uploaded = fls.upload()
      try:
        file_ = [fn for fn in uploaded.keys()][0] # Get the name of the uploaded file
        filename = input('File name (optional - ENTER to continue)') # The file name which you want to be
        if filename != '':
          format = input('Format (optional - ENTER to continue): ')
          if format != '' and f".{format}" not in filename: filename += f".{format}"
          else: format = file_[file_.find('.'):]
          try:
            os.system(f"sudo mv -f '{drive_path}/{file_}' '{self.path}/{filename}.{format}'")
          except: ERROR("Lol, you haven't uploaded any file yet.")
        else:
          os.system(f"sudo mv -f '{drive_path}/{file_}' '{self.path}/{file_}'")
        if file_[file_.find('.'):] == ".zip":
          if self.project_types == 'tmp': self.MODPACK(file_name= filename, software= software); sleep(40)
        else:
          saving_folder = input("In which folder should we save this file? For example: mods, . (main), etc...")
          if saving_folder != '.':
            saving_folder = f'{saving_folder}/'
            os.makedirs(f'{drive_path}/{server_name}/{saving_folder}', exist_ok=True)
            os.system(f"sudo mv -f '{self.path}/{file_}' '{drive_path}/{server_name}/{saving_folder}{file_}'")
        LOG('\nCompleted.')
      except: ERROR("Lol, you haven't uploaded any file yet.")
    elif self.choice == 'search':
      a = self.SEARCH(search_name, software)
      project = a[0]; project_names = a[1]; check = False;
      project_id = project[project_names]
      if software == 'Modrinth': rJSON = GET(f'https://api.modrinth.com/v2/project/{project_id}/version').json()
      else: rJSON = GET(f'https://api.curse.tools/v1/cf/mods/{project_id}').json()['data']['latestFilesIndexes'];
      for data in rJSON:
        if software == 'Curseforge': gameversions= data["gameVersion"]
        else: gameversions = data["game_versions"]
        if self.versions in gameversions:
          if software == 'Curseforge': files = data['filename']; url = f'https://www.curseforge.com/api/v1/mods/{project_id}/files/' + str(data['fileId']) + '/download'
          else: files = data['files'][0]['filename']; url = data['files'][0]['url']
          if ' ' in files: files = files.replace(' ', '_')
          DOWNLOAD_FILE(url= url, path = self.path, file_name= files)
          check = True
        if check == True and self.project_types == 'tmp':  self.MODPACK(file_name= files, software= software); LOG('\nCompleted.'); break
        elif check: LOG('\nCompleted.'); break
      if check == False: ERROR(f"It seems that {self.software} doesn't support this {self.project_types}")
    elif choice == 'install_geysermc':
      if self.categories != 'none' and self.colabconfig['tunnel_service'] in ["playit", "zrok"] and self.categories != 'forge':
        if 'velocity' in self.categories:
          rJSON = GET('https://api.papermc.io/v2/projects/velocity').json()['versions']
          if self.versions not in rJSON: ERROR('Not found versions')
          if exists(f'{drive_path}/{server_name}/plugins') == False:
            MKDIR(f'{drive_path}/{server_name}/plugins')
            sleep(40)
          plugin = ['ViaVersion', 'ViaBackwards']
          for plugins in plugin:
            rJSON = GET(f'https://hangar.papermc.io/api/v1/projects/{plugins}/versions').json()['result']; check = False
            if self.categories.upper() in rJSON[0]['platformDependenciesFormatted']:
              for hit in rJSON:
                if self.versions in hit['platformDependencies'][self.categories.upper()]:
                  DOWNLOAD_FILE(url = hit["downloads"][self.categories.upper()]["downloadUrl"], path = f'{drive_path}/{self.server_name}/plugins', file_name = hit['downloads'][self.categories.upper()]["fileInfo"]['name'])
                  check = True; break
              if check == False: LOG(f"{plugins} can't be downloaded in your velocity server. You can try to install and upload it through website")
              else: LOG(f'Installing {plugins} done. Try to install  for more supporter version')
            else: LOG(f"{plugins} can't be downloaded in your velocity server. You can try to install and upload it through website")
          DOWNLOAD_FILE(url = 'https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/velocity', path = f'{drive_path}/{server_name}/plugins', file_name = 'Geyser-Velocity.jar')
          LOG('You only need to install Floodgate on the BungeeCord or Velocity proxy server, unless you want to use the Floodgate API on the backend servers. Additionally, it will display Bedrock edition skins properly.')
          LOG('For more details. Check out https://wiki.geysermc.org/floodgate/setup/')
          DOWNLOAD_FILE(url = 'https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/velocity', path = f'{drive_path}/{server_name}/plugins', file_name = 'floodgate-velocity.jar')
        elif 'fabric' in self.categories:
          rJSON = GET('https://meta.fabricmc.net/v2/versions/game').json()['version']
          # warningserver_version
          if self.versions < rJSON and self.versions >= '1.17':
            LOG('Geyser-Fabric run only on 1.21.')
            LOG('To use Geyser with an older server version, you can use Geyser on a BungeeCord/Velocity proxy or Geyser-paper instead')
          else: ERROR(f"Your server_type isn't compatible for running Geyser_MC")
          LOG('Geyser only works with server-side mods. Mods that require a client-side install will not work!')
          LOG('Download geyser mc mods')
          DOWNLOAD_FILE(url = 'https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/fabric', path = f'{drive_path}/{self.server_name}/mods', file_name= 'Geyser-Fabric.jar')
          # Geyser mc mod require fabric api.
          LOG('Download Fabric api (requirement)')
          rJSON = GET(f'https://api.modrinth.com/v2/project/P7dR8mSH/version').json()
          check = False
          for data in rJSON:
            if self.versions in data["game_versions"]:
              files = data['files'][0]; url = files['url']
              DOWNLOAD_FILE(url= url, path = self.path, file_name= files['filename']); check = True
            if check: break
          if check == False: ERROR(f"It seems that theresn't fabric api for this minecraft version")
          LOG('Download floodgate')
          rJSON = GET(f'https://api.modrinth.com/v2/project/bWrNNfkb/version').json()
          check = False
          for data in rJSON:
            if versions in data["game_versions"]:
              files = data['files'][0]; url = files['url']
              DOWNLOAD_FILE(url= url, path = self.path, file_name= files['filename']); check = True
            if check: break
          if check == False: ERROR(f"It seems that theresn't fabric api for this minecraft version")
        elif 'paper' in self.categories or 'purpur' in self.categories:
          rJSON = GET('https://api.papermc.io/v2/projects/paper').json()['versions'][-1]
          if self.versions < rJSON and self.versions >= '1.17':
            plugin = ['ViaVersion', 'ViaBackwards']
            for plugins in plugin:
              rJSON = GET(f'https://hangar.papermc.io/api/v1/projects/{plugins}/versions').json()['result']; check = False
              if self.categories.upper() in rJSON[0]['platformDependenciesFormatted']:
                for hit in rJSON:
                  if self.versions in hit['platformDependencies'][self.categories.upper()]:
                    DOWNLOAD_FILE(url = hit["downloads"][self.categories.upper()]["downloadUrl"], path = f'{drive_path}/{self.server_name}/plugins', file_name = hit['downloads'][self.categories.upper()]["fileInfo"]['name'])
                    check = True; break
                if check == False: LOG(f"{plugins} can't be downloaded in your velocity server. You can try to install and upload it through website")
                else: LOG(f'Installing {plugins} done. Try to install  for more supporter version')
              else: LOG(f"{plugins} can't be downloaded in your velocity server. You can try to install and upload it through website")
          else: ERROR(f"Your server_type isn't compatible for running Geyser_MC")
          LOG('Download geyser mc plugin')
          DOWNLOAD_FILE(url = 'https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/spigot', path = f'{drive_path}/{self.server_name}/plugins', file_name= 'Geyser-Spigot.jar')
          LOG('Download floodgate')
          DOWNLOAD_FILE(url = 'https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/spigot', path = f'{drive_path}/{self.server_name}/plugins', file_name = 'floodgate-spigot.jar')
        # Set up notification
        self.colabconfig['Geysermc']= 'notdone'
        dump(self.colabconfig, open(COLABCONFIG(self.server_name),'w'))
        # Ping
        LOG('\nInstalling done. Try to rerun the server to configuration.')
      else: ERROR(f"Your server_type isn't compatible for running Geyser_MC")
    elif choice == 'dynmap support':
      if self.categories == 'paper' or self.categories == 'purpur' or self.categories == 'fabric' or self.categories == 'forge':
        if self.colabconfig['tunnel_service'] != 'ngrok' or self.colabconfig['tunnel_service'] != 'playit' or self.colabconfig['tunnel_service'] != 'localtonet' or self.colabconfig['tunnel_service'] !='zrok': ERROR('The server is not compatible to use dynmap')
        if self.categories == 'fabric' or self.categories == 'forge': software = 'Curseforge'
        else: software = 'Modrinth'
        check = False
        if software == 'Modrinth': project_id = 'fRQREgAc'; rJSON = GET(f'https://api.modrinth.com/v2/project/{project_id}/version').json()
        else: project_id = '59433'; rJSON = GET(f'https://api.curse.tools/v1/cf/mods/{project_id}').json()['data']['latestFilesIndexes'];
        for data in rJSON:
          if software == 'Curseforge': gameversions= data["gameVersion"]
          else: gameversions = data["game_versions"]
          if self.versions in gameversions:
            if software == 'Curseforge' and self.catergories in ganeversions[0]: url = f'https://www.curseforge.com/api/v1/mods/{project_id}/files/' + str(data['fileId']) + '/download'
            elif self.categories in data['loaders']: url = data['files'][0]['url']
            DOWNLOAD_FILE(url= url, path = self.path, file_name= 'dynmap-server.jar')
            check = True
          if check: LOG('\nCheck https://github.com/webbukkit/dynmap/wiki/Installation-Setup-of-Dynmap-on-Linux for more informations.'); LOG('\nWe have done the step 1 in the set up dynmap guide. Enjoy!'); break
        if check == False: ERROR(f"It seems that there is an error in installing dynmap plugin/mod")
      else: ERROR('Wrong choice')
