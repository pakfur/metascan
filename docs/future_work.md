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

- (V1) Refactor the search feature to (Content|Tag) to allow for searching by tag
  - In tag mode, the content search uses a drop-down prefix match on tag value
  - In tag mode, tags when selected are displayed as a removeable chip

- (V1) Configurable background Scan feature
  - Background worker process
  - Communicates to the front end over the /ws websocket
  - Watches the directories for new files and background processes them
  - Periodically scans for database/file system mismatches and cleans them up
  - A dialog box to show the state of the scanner (scanning, idle, history)

- (V1) Multi-select 
  - Either "select mode" like old Metascan or shift/alt select conventions

- (V1) Dockable right panel (docked, Floating (resizable), transparency setting)

- (V1+) Different slide show formats (full screen option, fade, themes)
  - Attach a slideshow settings to the library, or any folder
  - slideshow includes themes (ken burns, fade, slide)  

- (V1) Filters
  - Each filter type in an Accordian
  - add "Sort A-Z", "Sort # (9-0)" icon buttons for the filter content

- Filters UI
  - move filters to a drop down nexct to Favorites
  - filter drop down allows the selection of different filters using saved metadata (models, loras, camera stuff, tags, ...)
  - not persisted, applies to all views (ie filters apply over library/folder selections)


### Bugs

#### Media viewer
- (v1) Search by content
  - selected image is not preserved - can the selected image be scrolled to after viewing the content?
  - viewing searched for content exits the search mode. the search mode should be preserved until explicitly hitting exit

#### Configuration 
 - Directories
   - Missing a "Select folder" option (though how would that work remotely?)

#### Upscale processing dialog
  - Percent is orders of magnitude off (100 -> 4000%)

#### Upscaling 
  - after upscaling, the database metadata is not updated.
    - or it is not visible in the ui, even after a page refresh. should be updated in the ui via a /ws event


### Changes to existing features (Under consideration)
- (v1+) Replace favorite with Ranking Tier (S, A, B, C, D) with UI and ranking choice. "S" == Favorite

- Tagging
  - Need to improve content tagging
  - make tags per image editable, remove, add, create tag
  - Set a fixed value of tags, can we instead have a threshold so the proper number of relevant tags are extracted. 20 may be to many and too few for some pics

