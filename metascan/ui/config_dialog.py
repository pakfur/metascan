from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
    QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QFileDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from metascan.utils.app_paths import get_config_path
import os
import json
from typing import List, Dict


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.setModal(True)
        self.setFixedSize(600, 400)
        
        self.config_file = str(get_config_path())
        self.directories = []
        
        self._setup_ui()
        self._load_config()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Back button
        back_button = QPushButton("<<")
        back_button.setFixedWidth(60)
        back_button.clicked.connect(self.accept)
        layout.addWidget(back_button, alignment=Qt.AlignmentFlag.AlignLeft)
        
        # Table for directories
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Subfolders", "Path", ""])
        
        # Set column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(2, 50)
        
        layout.addWidget(self.table)
        
        # Add button
        add_button = QPushButton("Add")
        add_button.clicked.connect(self._add_directory)
        layout.addWidget(add_button)
        
        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self._save_and_close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
    def _add_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Directory")
        if folder:
            # Check if directory already exists
            for dir_info in self.directories:
                if dir_info['filepath'] == folder:
                    return
                    
            # Add to list
            self.directories.append({
                'filepath': folder,
                'search_subfolders': True
            })
            self._add_table_row(folder, True)
    
    def _add_table_row(self, path: str, search_subfolders: bool):
        row = self.table.rowCount()
        self.table.insertRow(row)
        
        # Checkbox for subfolders
        checkbox = QCheckBox()
        checkbox.setChecked(search_subfolders)
        checkbox.stateChanged.connect(lambda state, r=row: self._update_subfolders(r, state))
        self.table.setCellWidget(row, 0, checkbox)
        
        # Path
        self.table.setItem(row, 1, QTableWidgetItem(path))
        
        # Remove button
        remove_btn = QPushButton("X")
        remove_btn.setStyleSheet("QPushButton { background-color: #ff4444; color: white; font-weight: bold; }")
        remove_btn.setFixedSize(30, 30)
        remove_btn.clicked.connect(lambda checked, r=row: self._remove_directory(r))
        self.table.setCellWidget(row, 2, remove_btn)
    
    def _remove_directory(self, row: int):
        path = self.table.item(row, 1).text()
        self.directories = [d for d in self.directories if d['filepath'] != path]
        self.table.removeRow(row)
        
        # Update row numbers for remaining remove buttons
        for i in range(self.table.rowCount()):
            btn = self.table.cellWidget(i, 2)
            if btn:
                btn.clicked.disconnect()
                btn.clicked.connect(lambda checked, r=i: self._remove_directory(r))
    
    def _update_subfolders(self, row: int, state: int):
        path = self.table.item(row, 1).text()
        for dir_info in self.directories:
            if dir_info['filepath'] == path:
                dir_info['search_subfolders'] = (state == Qt.CheckState.Checked.value)
                break
    
    def _load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.directories = config.get('directories', [])
                    
                # Populate table
                for dir_info in self.directories:
                    self._add_table_row(dir_info['filepath'], dir_info['search_subfolders'])
            except Exception as e:
                print(f"Error loading config: {e}")
    
    def _save_and_close(self):
        self._save_config()
        self.accept()
    
    def _save_config(self):
        try:
            # Load existing config to preserve other settings
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
            
            # Update directories
            config['directories'] = self.directories
            
            # Save back to file
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
                
        except Exception as e:
            print(f"Error saving config: {e}")


    def get_directories(self) -> List[Dict[str, any]]:
        return self.directories