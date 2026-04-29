## Todo

### New Features
- (V1) Make a background worker to watch the directories and scan/import new files automatically
  - Configurable, enable or disable


- (V1) Worker Watcher - A worker watch that monitors all workers
  - Tab that displays a list of workers
  - Color dot for status - green running/healthy, yellow running/warn, red errors, white not running/healthy
  - show current activity per worker
  - show logs per worker, copy, scroll, clear, search

- (V1) Add configuration option tab to the configuration dialog to disable CLIP, pHash, and embedding.
  - When disabled, then any UI component that depends on CLIP, pHash or embedding will be disabled

- (MVP) Refactor the search feature to (Content|Tag) to allow for searching by tag
  - In tag mode, the content search uses a drop-down prefix match on tag value
  - In tag mode, tags when selected are displayed as a removeable chip

- (v1) Configurable background Scan feature
  - Background worker process
  - Communicates to the front end over the /ws websocket
  - Watches the directories for new files and background processes them
  - Periodically scans for database/file system mismatches and cleans them up
  - A dialog box to show the state of the scanner (scanning, idle, history)

- (V1) Multi-select 
  - Either "select mode" like old Metascan or shift/alt select conventions

- (v1) Dockable right panel (docked, Floating (resizable), transparency setting)

- (V1+) Different media viewer formats (compact, carousel)
  - Compact: metadata (favorite icon, file name, video/image icon) on the iumage, narrow padding between media (what it dies today)
  - Loose:  metadata (favorite icon, file name, video/image icon) on the iumage, larger padding between media (new)
  - Carousel: metadata below the media, card-like format (new)
  - Drop down icon menu to select. Selection choice is saved in config.json and preserved during a restart.

X (MVP) PrimeVue Galleria below the media in the media viewer
  - Galleria of previous and next thumbnails below the media  

- (MVP) Filters
  - Each filter type in an Accordian
  - add "Sort A-Z", "Sort # (9-0)" icon buttons for the filter content


X (v1) Folders and Smart folders
  X Virtual folders. Allows for manual organization of media into folders. Many to many relationship between media files and folders. Folders get names, and clicking on one acts like a filter
  X Smart folders. Associated with contentr search strings and/or tags. Files that meet the criteria automatically get added to the folder. Clicking on one acts like a filter. Content is scanned in the background and associated with a Smart folder.

- (v1+) Geo metadata awareness. Support geo location of images on a global, zoomable map. Search for images associated with a geolocation. Can use metadata or manually assign a location to an image


### Bugs

#### Media viewer
X (MVP) videos should auto-replay in the media viewer
X (MVP) slideshow videos should autoplay
X (MVP) Details panel copy to clipboard for individual or all does not work
- (v1) Search by content
  - selected image is not preserved - can the selected image be scrolled to after viewing the content?
  - viewing searched for content exits the search mode. the search mode should be preserved until explicitly hitting exit
- (MVP) Details screen
  - The details panel needs to scroll
  - Panel spacing can get squished if space is too small
  - Detail panel title bars must be fixed height
  - Detail panel content cards need scrolling content

#### Configuration 
 - Directories
   - Missing a "Select folder" option (though how would that work remotely?)

#### Main screen
X (MVP) arrow keys are not changing the selected media


#### Changes to existing features (Under consideration)
X (MVP) load content search (CLIP) model at backend service start
- (v1+) Replace favorite with Ranking Tier (S, A, B, C, D) with UI and ranking choice. "S" == Favorite

