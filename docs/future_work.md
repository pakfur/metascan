## Todo


### New Features
- Add configuration option to disable CLIP, pHash, and embedding.

- Add search option (Content|Tag) to allow for searching by tag

- Configurable background Scan feature
  - Background worker process
  - Communicates to the front end over the /ws websocket
  - Watches the directories for new files and background processes them
  - Periodically scans for database/file system mismatches and cleans them up
  - A dialog box to show the state of the scanner (scanning, idle, history)

- Multi-select 
  - Either "select mode" like old Metascan or shift/alt select conventions

- Dockable right panel (docked, Floating (resizable), transparency setting)

- Different media viewer formats (compact, carousel)
  - Compact: metadata (favorite icon, file name, video/image icon) on the iumage, narrow padding between media
  - Loose:  metadata (favorite icon, file name, video/image icon) on the iumage, larger padding between media
  - Carousel: metadata below the media, card-like format

- Galleria below the media viewer
  - Scroll of previous and next thumbnails below the media  


### Bugs

#### Media viewer
- videos should auto-replay in the media viewer
- slideshow videos should autoplay

#### Main screen
- arrow keys are not changing the selected media


#### Changes to existing features (Under consideration)
- load content search (CLIP) model at backend service start
- Replace favorite with Ranking Tier (S, A, B, C, D) with UI and ranking choice. "S" == Favorite

