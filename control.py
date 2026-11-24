"""
Dhan Options Control GUI - PyQt5 Version
"""

import sys
import logging
import subprocess
import webbrowser
import socket
import os
import signal
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from PyQt5.QtWidgets import (QApplication, QMainWindow, QMessageBox, QWidget, 
                             QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton,
                             QListWidget, QFileDialog, QGroupBox, QAbstractItemView,
                             QComboBox, QTextEdit, QDateTimeEdit, QSpinBox, QCheckBox, 
                             QTabWidget, QTimeEdit, QRadioButton, QButtonGroup, QDateEdit,
                             QProgressBar, QSplitter)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QDateTime, QTime, QDate
from PyQt5.QtGui import QFont

# Import the engines and archive manager
try:
    from engine import PersonalDhanClient, AssetConfig
    from archive_manager import DatabaseArchiver, ArchiveStats, OHLCDatabaseArchiver, OHLCArchiveStats
    from ohlc_engine import OHLCEngine
except ImportError as e:
    print(f"Cannot import required modules. Ensure they're in the same directory. Error: {e}")
    sys.exit(1)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ScheduleManager:
    """Manages scheduling logic for continuous collection"""
    
    def __init__(self, config_file_path: str = "schedule_config.json"):
        self.config_file = config_file_path
        self.schedule_config = self.load_schedule_config()
    
    def load_schedule_config(self) -> Dict:
        """Load schedule configuration from JSON file"""
        default_config = {
            'enabled': False,
            'start_time': '09:15',  # Format: HH:MM
            'end_time': '15:30',
            'schedule_type': 'daily',  # 'once', 'daily', 'weekdays'
            'schedule_date': None,  # For 'once' type: YYYY-MM-DD
            'last_modified': None
        }
        
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    loaded_config = json.load(f)
                    # Merge with defaults to handle missing keys
                    default_config.update(loaded_config)
            return default_config
        except Exception as e:
            logger.error(f"Error loading schedule config: {e}")
            return default_config
    
    def save_schedule_config(self):
        """Save current schedule configuration to JSON file"""
        try:
            self.schedule_config['last_modified'] = datetime.now().isoformat()
            with open(self.config_file, 'w') as f:
                json.dump(self.schedule_config, f, indent=2)
            logger.info(f"Schedule configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving schedule config: {e}")
    
    def update_schedule(self, enabled: bool, start_time: str, end_time: str, 
                       schedule_type: str, schedule_date: str = None):
        """Update schedule configuration"""
        self.schedule_config.update({
            'enabled': enabled,
            'start_time': start_time,
            'end_time': end_time,
            'schedule_type': schedule_type,
            'schedule_date': schedule_date
        })
        self.save_schedule_config()
    
    def should_start_collection(self) -> bool:
        """Check if collection should start now"""
        if not self.schedule_config['enabled']:
            return False
        
        now = datetime.now()
        
        # Check if we're in the right day based on schedule type
        if not self._is_scheduled_day(now):
            return False
        
        # Check if we're at or past start time
        start_time = self._parse_time(self.schedule_config['start_time'])
        current_time = now.time()
        
        return current_time >= start_time
    
    def is_within_collection_window(self) -> bool:
        """Check if current time is within collection window"""
        if not self.schedule_config['enabled']:
            return False
        
        now = datetime.now()
        
        # Check if we're in the right day based on schedule type
        if not self._is_scheduled_day(now):
            return False
        
        # Check if we're within time window
        start_time = self._parse_time(self.schedule_config['start_time'])
        end_time = self._parse_time(self.schedule_config['end_time'])
        current_time = now.time()
        
        # Handle overnight windows (though unlikely for trading hours)
        if start_time <= end_time:
            return start_time <= current_time <= end_time
        else:
            return current_time >= start_time or current_time <= end_time
    
    def get_next_collection_start(self) -> Optional[datetime]:
        """Get next scheduled collection start time"""
        if not self.schedule_config['enabled']:
            return None
        
        now = datetime.now()
        start_time = self._parse_time(self.schedule_config['start_time'])
        
        if self.schedule_config['schedule_type'] == 'once':
            if not self.schedule_config['schedule_date']:
                return None
            
            schedule_date = datetime.strptime(self.schedule_config['schedule_date'], '%Y-%m-%d').date()
            next_start = datetime.combine(schedule_date, start_time)
            
            # Only return if it's in the future
            return next_start if next_start > now else None
        
        elif self.schedule_config['schedule_type'] == 'daily':
            # Today's start time
            today_start = datetime.combine(now.date(), start_time)
            if today_start > now:
                return today_start
            
            # Tomorrow's start time
            return datetime.combine(now.date() + timedelta(days=1), start_time)
        
        elif self.schedule_config['schedule_type'] == 'weekdays':
            # Find next weekday
            days_ahead = 0
            while True:
                check_date = now.date() + timedelta(days=days_ahead)
                check_datetime = datetime.combine(check_date, start_time)
                
                # Monday = 0, Sunday = 6
                if check_date.weekday() < 5:  # Monday to Friday
                    if check_datetime > now:
                        return check_datetime
                
                days_ahead += 1
                if days_ahead > 7:  # Safety break
                    break
        
        return None
    
    def _is_scheduled_day(self, check_datetime: datetime) -> bool:
        """Check if given datetime falls on a scheduled day"""
        if self.schedule_config['schedule_type'] == 'once':
            if not self.schedule_config['schedule_date']:
                return False
            schedule_date = datetime.strptime(self.schedule_config['schedule_date'], '%Y-%m-%d').date()
            return check_datetime.date() == schedule_date
        
        elif self.schedule_config['schedule_type'] == 'daily':
            return True
        
        elif self.schedule_config['schedule_type'] == 'weekdays':
            # Monday = 0, Sunday = 6
            return check_datetime.weekday() < 5  # Monday to Friday
        
        return False
    
    def _parse_time(self, time_str: str) -> time:
        """Parse time string in HH:MM format"""
        try:
            return datetime.strptime(time_str, '%H:%M').time()
        except ValueError:
            logger.error(f"Invalid time format: {time_str}")
            return datetime.strptime('09:15', '%H:%M').time()  # Default fallback
    
    def get_status_text(self) -> str:
        """Get human-readable status text for UI"""
        if not self.schedule_config['enabled']:
            return "Scheduling disabled"
        
        next_start = self.get_next_collection_start()
        if not next_start:
            return "No future collection scheduled"
        
        if self.is_within_collection_window():
            end_time = self.schedule_config['end_time']
            return f"Active until {end_time}"
        
        # Format next start time nicely
        if next_start.date() == datetime.now().date():
            return f"Next: Today {next_start.strftime('%H:%M')}"
        elif next_start.date() == datetime.now().date() + timedelta(days=1):
            return f"Next: Tomorrow {next_start.strftime('%H:%M')}"
        else:
            return f"Next: {next_start.strftime('%a %b %d, %H:%M')}"


class OHLCScheduleManager:
    """Manages scheduling logic for OHLC continuous collection"""

    def __init__(self, config_file_path: str = "ohlc_schedule_config.json"):
        self.config_file = config_file_path
        self.schedule_config = self.load_schedule_config()

    def load_schedule_config(self) -> Dict:
        """Load schedule configuration from JSON file"""
        default_config = {
            'enabled': False,
            'start_time': '09:15',  # Format: HH:MM
            'end_time': '15:30',
            'schedule_type': 'weekdays',  # 'daily', 'weekdays'
            'last_modified': None
        }

        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    loaded_config = json.load(f)
                    # Merge with defaults to handle missing keys
                    default_config.update(loaded_config)
            return default_config
        except Exception as e:
            logger.error(f"Error loading OHLC schedule config: {e}")
            return default_config

    def save_schedule_config(self):
        """Save current schedule configuration to JSON file"""
        try:
            self.schedule_config['last_modified'] = datetime.now().isoformat()
            with open(self.config_file, 'w') as f:
                json.dump(self.schedule_config, f, indent=2)
            logger.info(f"OHLC schedule configuration saved to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving OHLC schedule config: {e}")

    def update_schedule(self, enabled: bool, start_time: str, end_time: str, schedule_type: str):
        """Update schedule configuration"""
        self.schedule_config.update({
            'enabled': enabled,
            'start_time': start_time,
            'end_time': end_time,
            'schedule_type': schedule_type
        })
        self.save_schedule_config()

    def should_start_collection(self) -> bool:
        """Check if collection should start now"""
        if not self.schedule_config['enabled']:
            return False

        now = datetime.now()

        # Check if we're in the right day based on schedule type
        if not self._is_scheduled_day(now):
            return False

        # Check if we're at or past start time
        start_time = self._parse_time(self.schedule_config['start_time'])
        current_time = now.time()

        return current_time >= start_time

    def is_within_collection_window(self) -> bool:
        """Check if current time is within collection window"""
        if not self.schedule_config['enabled']:
            return False

        now = datetime.now()

        # Check if we're in the right day based on schedule type
        if not self._is_scheduled_day(now):
            return False

        # Check if we're within time window
        start_time = self._parse_time(self.schedule_config['start_time'])
        end_time = self._parse_time(self.schedule_config['end_time'])
        current_time = now.time()

        # Handle overnight windows (though unlikely for trading hours)
        if start_time <= end_time:
            return start_time <= current_time <= end_time
        else:
            return current_time >= start_time or current_time <= end_time

    def get_next_collection_start(self) -> Optional[datetime]:
        """Get next scheduled collection start time"""
        if not self.schedule_config['enabled']:
            return None

        now = datetime.now()
        start_time = self._parse_time(self.schedule_config['start_time'])

        if self.schedule_config['schedule_type'] == 'daily':
            # Today's start time
            today_start = datetime.combine(now.date(), start_time)
            if today_start > now:
                return today_start

            # Tomorrow's start time
            return datetime.combine(now.date() + timedelta(days=1), start_time)

        elif self.schedule_config['schedule_type'] == 'weekdays':
            # Find next weekday
            days_ahead = 0
            while True:
                check_date = now.date() + timedelta(days=days_ahead)
                check_datetime = datetime.combine(check_date, start_time)

                # Monday = 0, Sunday = 6
                if check_date.weekday() < 5:  # Monday to Friday
                    if check_datetime > now:
                        return check_datetime

                days_ahead += 1
                if days_ahead > 7:  # Safety break
                    break

        return None

    def _is_scheduled_day(self, check_datetime: datetime) -> bool:
        """Check if given datetime falls on a scheduled day"""
        if self.schedule_config['schedule_type'] == 'daily':
            return True

        elif self.schedule_config['schedule_type'] == 'weekdays':
            # Monday = 0, Sunday = 6
            return check_datetime.weekday() < 5  # Monday to Friday

        return False

    def _parse_time(self, time_str: str) -> time:
        """Parse time string in HH:MM format"""
        try:
            return datetime.strptime(time_str, '%H:%M').time()
        except ValueError:
            logger.error(f"Invalid time format: {time_str}")
            return datetime.strptime('09:15', '%H:%M').time()  # Default fallback

    def get_status_text(self) -> str:
        """Get human-readable status text for UI"""
        if not self.schedule_config['enabled']:
            return "OHLC scheduling disabled"

        next_start = self.get_next_collection_start()
        if not next_start:
            return "No future OHLC collection scheduled"

        if self.is_within_collection_window():
            end_time = self.schedule_config['end_time']
            return f"OHLC active until {end_time}"

        # Format next start time nicely
        if next_start.date() == datetime.now().date():
            return f"Next OHLC: Today {next_start.strftime('%H:%M')}"
        elif next_start.date() == datetime.now().date() + timedelta(days=1):
            return f"Next OHLC: Tomorrow {next_start.strftime('%H:%M')}"
        else:
            return f"Next OHLC: {next_start.strftime('%a %b %d, %H:%M')}"


class DhanControlGUI(QMainWindow):
    """PyQt5 version of Dhan options data control GUI"""
    
    def __init__(self):
        super().__init__()

        # Initialize engine first
        try:
            start_time = time.time()
            logger.info("Starting PersonalDhanClient initialization...")
            self.engine = PersonalDhanClient()
            elapsed = time.time() - start_time
            logger.info(f"Engine initialized successfully in {elapsed:.2f}s")
        except Exception as e:
            QMessageBox.critical(None, "Engine Error", f"Failed to initialize engine: {e}")
            sys.exit(1)

        # Initialize archive manager
        try:
            start_time = time.time()
            logger.info("Starting DatabaseArchiver initialization...")
            self.archiver = DatabaseArchiver(self.engine.db_path)
            elapsed = time.time() - start_time
            logger.info(f"Archive manager initialized successfully in {elapsed:.2f}s")
        except Exception as e:
            QMessageBox.critical(None, "Archive Error", f"Failed to initialize archive manager: {e}")
            sys.exit(1)

        # Initialize OHLC engine
        try:
            start_time = time.time()
            logger.info("Starting OHLC engine initialization...")
            self.ohlc_engine = OHLCEngine()  # Auto-loads assets_select.csv
            elapsed = time.time() - start_time
            logger.info(f"OHLC engine initialized successfully in {elapsed:.2f}s")
        except Exception as e:
            QMessageBox.critical(None, "OHLC Engine Error", f"Failed to initialize OHLC engine: {e}")
            sys.exit(1)

        # OHLC archive manager will be initialized after GUI shows (it takes ~15s)
        self.ohlc_archiver = None

        self.setWindowTitle("Data Bandar by ChorBazaar")
        self.setGeometry(100, 100, 800, 600)
        self.setMinimumSize(600, 600)

        # Initialize data
        self.loaded_assets = []
        self.csv_file_path = ""

        # Initialize OHLC data
        self.ohlc_loaded_assets = []
        self.ohlc_csv_file_path = ""

        # Initialize schedule manager (for OI Engine)
        self.schedule_manager = ScheduleManager()

        # Initialize OHLC schedule manager
        self.ohlc_schedule_manager = OHLCScheduleManager()
        
        # Collection state
        self.collection_stats = {
            'total': 0,
            'completed': 0,
            'success': 0,
            'failed': 0,
            'current_asset': None
        }
        
        # Scheduling state
        self.continuous_collection_thread = None
        self.schedule_check_timer = QTimer()
        self.schedule_check_timer.timeout.connect(self.check_schedule_trigger)
        self.schedule_check_timer.timeout.connect(self.check_ohlc_schedule_trigger)  # Also check OHLC schedule
        self.schedule_check_timer.start(60000)  # Check every minute
        

        self.setup_gui()
        self.setup_update_timer()

        # Defer heavy initialization until after GUI is shown to prevent blocking
        QTimer.singleShot(0, self._initialize_data_after_show)

        logger.info("PyQt5 GUI with header panel created")

    def _initialize_data_after_show(self):
        """Initialize data after GUI is shown in background thread"""
        logger.info("Starting background data initialization...")

        # Start initialization worker thread
        self.init_worker = InitializationWorker(self.engine, self.ohlc_engine, self.archiver)
        self.init_worker.oi_assets_loaded.connect(self.on_oi_assets_loaded)
        self.init_worker.ohlc_assets_loaded.connect(self.on_ohlc_assets_loaded)
        self.init_worker.archive_assets_loaded.connect(self.on_archive_assets_loaded)
        self.init_worker.init_complete.connect(self.on_init_complete)
        self.init_worker.init_error.connect(self.on_init_error)
        self.init_worker.start()

        # Initialize OHLC archive manager after a short delay (takes ~15s, but GUI is now responsive)
        QTimer.singleShot(500, self._initialize_ohlc_archiver)

    def on_oi_assets_loaded(self, loaded_assets, csv_path):
        """Handle OI assets loaded from background thread"""
        self.loaded_assets = loaded_assets
        self.csv_file_path = csv_path

        # Update GUI
        filename = os.path.basename(csv_path)
        self.csv_path_label.setText(f"File: {filename}")
        self.csv_path_label.setStyleSheet("color: green; font-weight: bold;")

        # Update asset list with sorting
        self.asset_list.clear()
        idx_assets = []
        nse_eq_assets = []
        other_assets = []

        for asset in loaded_assets:
            if asset.segment_code == 'IDX_I':
                idx_assets.append(asset)
            elif asset.segment_code == 'NSE_EQ':
                nse_eq_assets.append(asset)
            else:
                other_assets.append(asset)

        idx_assets.sort(key=lambda x: x.asset_name)
        nse_eq_assets.sort(key=lambda x: x.asset_name)
        other_assets.sort(key=lambda x: x.asset_name)

        for asset in idx_assets + nse_eq_assets + other_assets:
            self.asset_list.addItem(asset.asset_name)

        self.asset_count_label.setText(f"Loaded: {len(loaded_assets)} assets")
        self.log_status(f"Loaded {len(loaded_assets)} assets from {filename}")

    def on_ohlc_assets_loaded(self, ohlc_loaded_assets):
        """Handle OHLC assets loaded from background thread"""
        self.ohlc_loaded_assets = ohlc_loaded_assets

        # Update GUI
        self.ohlc_asset_list.clear()
        idx_assets = []
        nse_eq_assets = []
        other_assets = []

        for asset in ohlc_loaded_assets:
            if asset['segment_code'] == 'IDX_I':
                idx_assets.append(asset)
            elif asset['segment_code'] == 'NSE_EQ':
                nse_eq_assets.append(asset)
            else:
                other_assets.append(asset)

        idx_assets.sort(key=lambda x: x['asset_name'])
        nse_eq_assets.sort(key=lambda x: x['asset_name'])
        other_assets.sort(key=lambda x: x['asset_name'])

        for asset in idx_assets + nse_eq_assets + other_assets:
            self.ohlc_asset_list.addItem(asset['asset_name'])

        # Update OHLC archive asset list
        self.populate_ohlc_archive_assets()

        self.ohlc_csv_file_path = "assets/assets_select.csv"
        self.ohlc_csv_path_label.setText("File: assets_select.csv")
        self.ohlc_csv_path_label.setStyleSheet("color: green; font-weight: bold;")
        self.ohlc_asset_count_label.setText(f"Loaded: {len(ohlc_loaded_assets)} assets")
        self.log_ohlc_status(f"Auto-loaded {len(ohlc_loaded_assets)} OHLC assets")

    def on_archive_assets_loaded(self, available_assets):
        """Handle archive assets loaded from background thread"""
        self.archive_asset_list.clear()
        self.archive_asset_mapping = {}

        idx_assets = []
        other_assets = []

        for asset_info in available_assets:
            display_name = asset_info['display_name']
            underlying_symbol = asset_info['underlying_symbol']
            self.archive_asset_mapping[display_name] = underlying_symbol

            if 'IDX_I' in underlying_symbol:
                idx_assets.append(display_name)
            else:
                other_assets.append(display_name)

        idx_assets.sort()
        other_assets.sort()

        for display_name in idx_assets:
            self.archive_asset_list.addItem(display_name)
        for display_name in other_assets:
            self.archive_asset_list.addItem(display_name)

        if available_assets:
            idx_count = len(idx_assets)
            other_count = len(other_assets)
            status_msg = f"{len(available_assets)} assets available ({idx_count} indices, {other_count} stocks)"
            self.archive_status_label.setText(status_msg)
        else:
            self.archive_status_label.setText("No assets found in database")

    def on_init_complete(self):
        """Handle initialization completion"""
        logger.info("Background initialization completed successfully")

    def on_init_error(self, operation_name, error_msg):
        """Handle initialization error"""
        logger.error(f"Initialization error in {operation_name}: {error_msg}")
        self.log_status(f"Error loading {operation_name}: {error_msg}")

    def _initialize_ohlc_archiver(self):
        """Initialize OHLC archive manager in background thread (slow operation - deferred)"""
        logger.info("Starting OHLC archive manager initialization in background...")

        # Create background thread for initialization
        self.ohlc_archiver_init_thread = QThread()

        def init_archiver():
            try:
                start_time = time.time()
                ohlc_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ohlc.db")
                archiver = OHLCDatabaseArchiver(ohlc_db_path)
                elapsed = time.time() - start_time
                # Signal back to main thread
                QTimer.singleShot(0, lambda: self._on_ohlc_archiver_ready(archiver, elapsed))
            except Exception as e:
                logger.error(f"Failed to initialize OHLC archive manager: {e}")
                QTimer.singleShot(0, lambda: self._on_ohlc_archiver_error(str(e)))

        self.ohlc_archiver_init_thread.run = init_archiver
        self.ohlc_archiver_init_thread.start()

    def _on_ohlc_archiver_ready(self, archiver, elapsed):
        """Callback when OHLC archiver is ready"""
        self.ohlc_archiver = archiver
        logger.info(f"OHLC archive manager initialized successfully in {elapsed:.2f}s")

        # Now populate the OHLC archive asset list
        self.populate_ohlc_archive_assets()

    def _on_ohlc_archiver_error(self, error_msg):
        """Callback when OHLC archiver initialization fails"""
        logger.error(f"OHLC archive manager initialization failed: {error_msg}")
        QMessageBox.warning(self, "OHLC Archive Warning",
                          f"OHLC archive functionality may be limited: {error_msg}")
    
    def setup_gui(self):
        """Setup the GUI layout with tabs"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)

        # Create tab widget
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        # Create tabs
        self.oi_tab()
        self.ohlc_tab()

    def oi_tab(self):
        """Create OI Engine tab with all current functionality"""
        oi_widget = QWidget()

        tab_layout = QVBoxLayout()
        oi_widget.setLayout(tab_layout)

        # Create header panel
        self.create_header_panel(tab_layout)

        # Create main content area with titles and panels
        content_layout = QVBoxLayout()

        # Panel titles row
        titles_layout = QHBoxLayout()

        asset_title = QLabel("Asset Management")
        asset_title.setStyleSheet("color: green; font-weight: bold;")
        titles_layout.addWidget(asset_title)

        archive_title = QLabel("Database Archive")
        archive_title.setStyleSheet("color: green; font-weight: bold;")
        titles_layout.addWidget(archive_title)

        content_layout.addLayout(titles_layout)

        # Panels row
        panels_layout = QHBoxLayout()

        # Create left panel (asset management) without title
        self.create_left_panel(panels_layout)

        # Create right panel (archive and progress) without title
        self.create_right_panel_simplified(panels_layout)

        content_layout.addLayout(panels_layout, 1)
        tab_layout.addLayout(content_layout, 1)

        # Add tab
        self.tab_widget.addTab(oi_widget, "OI Engine")

    def ohlc_tab(self):
        """Create OHLC Engine tab with similar structure"""
        ohlc_widget = QWidget()

        tab_layout = QVBoxLayout()
        ohlc_widget.setLayout(tab_layout)

        # Create OHLC header panel
        self.ohlc_header_panel(tab_layout)

        # Create main content area with titles and panels
        content_layout = QVBoxLayout()

        # Panel titles row
        titles_layout = QHBoxLayout()

        asset_title = QLabel("OHLC Asset Management")
        asset_title.setStyleSheet("color: green; font-weight: bold;")
        titles_layout.addWidget(asset_title)

        activity_title = QLabel("OHLC Activity Monitor")
        activity_title.setStyleSheet("color: green; font-weight: bold;")
        titles_layout.addWidget(activity_title)

        content_layout.addLayout(titles_layout)

        # Panels row
        panels_layout = QHBoxLayout()

        # Create OHLC panels
        self.ohlc_asset_panel(panels_layout)
        self.ohlc_activity_panel(panels_layout)

        content_layout.addLayout(panels_layout, 1)
        tab_layout.addLayout(content_layout, 1)

        # Add tab
        self.tab_widget.addTab(ohlc_widget, "OHLC Engine")

    def create_header_panel(self, parent_layout):
        """Create header status panel"""
        header_frame = QFrame()
        header_frame.setFrameStyle(QFrame.Box | QFrame.Raised)
        header_frame.setLineWidth(1)
        header_frame.setFixedHeight(80)
        
        header_layout = QVBoxLayout()
        header_frame.setLayout(header_layout)
        
        # Top row
        top_row = QHBoxLayout()
        
        self.api_status_label = QLabel("API Status: ●")
        self.api_status_label.setStyleSheet("color: green;")
        top_row.addWidget(self.api_status_label)
        
        top_row.addWidget(QLabel(" | "))
        
        self.success_rate_label = QLabel("Success Rate: 0%")
        top_row.addWidget(self.success_rate_label)
        
        top_row.addWidget(QLabel(" | "))
        
        self.db_records_label = QLabel("DB Records: 0")
        top_row.addWidget(self.db_records_label)
        
        top_row.addStretch()  # Push everything to left
        
        # Archive and CSV buttons at extreme right
        self.create_archive_header_btn = QPushButton("Create Archive")
        self.create_archive_header_btn.clicked.connect(self.create_database_archive)
        self.create_archive_header_btn.setMinimumHeight(35)
        self.create_archive_header_btn.setStyleSheet("QPushButton { background-color: #28a745; color: white; font-weight: bold; }")
        top_row.addWidget(self.create_archive_header_btn)
        
        self.load_csv_btn = QPushButton("Load CSV")
        self.load_csv_btn.clicked.connect(self.load_csv_file)
        self.load_csv_btn.setMinimumHeight(35)
        self.load_csv_btn.setStyleSheet("QPushButton { background-color: #007bff; color: white; font-weight: bold; }")
        top_row.addWidget(self.load_csv_btn)
        
        header_layout.addLayout(top_row)
        
        # Bottom row
        bottom_row = QHBoxLayout()
        
        self.adaptive_delay_label = QLabel("Adaptive Delay: 0s")
        bottom_row.addWidget(self.adaptive_delay_label)
        
        bottom_row.addWidget(QLabel(" | "))
        
        self.last_error_label = QLabel("Last Error: None")
        bottom_row.addWidget(self.last_error_label)
        
        bottom_row.addStretch()  # Push everything to left
        header_layout.addLayout(bottom_row)
        
        parent_layout.addWidget(header_frame)
    
    def create_left_panel(self, parent_layout):
        """Create asset management panel with new simplified layout"""
        # Create group box without title (title is now external)
        asset_group = QGroupBox()
        
        asset_layout = QVBoxLayout()
        asset_group.setLayout(asset_layout)
        
        # Asset list
        self.asset_list = QListWidget()
        self.asset_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.asset_list.setMaximumHeight(300)
        self.asset_list.itemSelectionChanged.connect(self.update_selection_display)
        asset_layout.addWidget(self.asset_list)
        
        # File path and asset count in same row (moved below asset list)
        file_info_layout = QHBoxLayout()
        
        self.csv_path_label = QLabel("No file selected")
        self.csv_path_label.setStyleSheet("color: gray;")
        file_info_layout.addWidget(self.csv_path_label)
        
        file_info_layout.addStretch()  # Space between file and count
        
        self.asset_count_label = QLabel("Loaded: 0 assets")
        file_info_layout.addWidget(self.asset_count_label)
        
        asset_layout.addLayout(file_info_layout)
        
        # Row 1: All action buttons in one row
        buttons_layout = QHBoxLayout()
        
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all_assets)
        buttons_layout.addWidget(self.select_all_btn)
        
        self.clear_selection_btn = QPushButton("Clear")
        self.clear_selection_btn.clicked.connect(self.clear_asset_selection)
        buttons_layout.addWidget(self.clear_selection_btn)
        
        self.collect_btn = QPushButton("Collect")
        self.collect_btn.clicked.connect(self.collect_multiple_assets)  # Same as old "Collect Selected"
        buttons_layout.addWidget(self.collect_btn)
        
        # Schedule checkbox 
        self.schedule_checkbox = QCheckBox("Schedule")
        self.schedule_checkbox.setChecked(self.schedule_manager.schedule_config['enabled'])
        self.schedule_checkbox.stateChanged.connect(self.on_schedule_enabled_changed)
        buttons_layout.addWidget(self.schedule_checkbox)
        
        buttons_layout.addStretch()  # Push everything to left
        asset_layout.addLayout(buttons_layout)
        
        # Schedule controls under Collect button
        schedule_controls_layout = QHBoxLayout()
        
        # Left column: Start and End time dropdowns
        left_column = QVBoxLayout()
        
        self.start_time_combo = QComboBox()
        self.populate_time_combo(self.start_time_combo)
        self.set_combo_time(self.start_time_combo, self.schedule_manager.schedule_config['start_time'])
        self.start_time_combo.currentTextChanged.connect(self.on_schedule_time_changed)
        left_column.addWidget(QLabel("Start Time:"))
        left_column.addWidget(self.start_time_combo)
        
        self.end_time_combo = QComboBox()
        self.populate_time_combo(self.end_time_combo)
        self.set_combo_time(self.end_time_combo, self.schedule_manager.schedule_config['end_time'])
        self.end_time_combo.currentTextChanged.connect(self.on_schedule_time_changed)
        left_column.addWidget(QLabel("End Time:"))
        left_column.addWidget(self.end_time_combo)
        
        # Right column: Frequency dropdown
        right_column = QVBoxLayout()

        self.frequency_combo = QComboBox()
        self.frequency_combo.addItems(["Daily", "Weekdays"])
        self.set_frequency_combo()
        self.frequency_combo.currentTextChanged.connect(self.on_schedule_type_changed)
        right_column.addWidget(QLabel("Frequency:"))
        right_column.addWidget(self.frequency_combo)

        right_column.addStretch()  # Fill remaining space

        schedule_controls_layout.addLayout(left_column)
        schedule_controls_layout.addLayout(right_column)
        asset_layout.addLayout(schedule_controls_layout)
        
        # Schedule buttons
        schedule_buttons_layout = QHBoxLayout()
        
        self.start_schedule_btn = QPushButton("Start Schedule")
        self.start_schedule_btn.clicked.connect(self.start_continuous_collection)
        schedule_buttons_layout.addWidget(self.start_schedule_btn)
        
        self.stop_schedule_btn = QPushButton("Stop Schedule")
        self.stop_schedule_btn.clicked.connect(self.stop_continuous_collection)
        self.stop_schedule_btn.setEnabled(False)
        schedule_buttons_layout.addWidget(self.stop_schedule_btn)
        
        schedule_buttons_layout.addStretch()  # Push buttons to left
        asset_layout.addLayout(schedule_buttons_layout)
        
        
        # Row 3: Cache management buttons
        cache_layout = QHBoxLayout()
        
        self.refresh_cache_btn = QPushButton("Refresh All Expiries")
        self.refresh_cache_btn.clicked.connect(self.refresh_expiry_cache)
        self.refresh_cache_btn.setMinimumHeight(35)
        self.refresh_cache_btn.setStyleSheet("QPushButton { background-color: #17a2b8; color: white; font-weight: bold; }")
        cache_layout.addWidget(self.refresh_cache_btn)
        
        self.clear_cache_btn = QPushButton("Clear Cache")
        self.clear_cache_btn.clicked.connect(self.clear_expiry_cache)
        self.clear_cache_btn.setMinimumHeight(35)
        self.clear_cache_btn.setStyleSheet("QPushButton { background-color: #dc3545; color: white; font-weight: bold; }")
        cache_layout.addWidget(self.clear_cache_btn)
        
        asset_layout.addLayout(cache_layout)
        
        # Cache status display (moved from Analytics tab)
        self.cache_status_label = QLabel("Cache: Loading...")
        self.cache_status_label.setStyleSheet("font-weight: bold; color: green; padding: 5px;")
        asset_layout.addWidget(self.cache_status_label)
        
        # Expiry configuration info
        expiry_info_label = QLabel("Expiry counts configured per asset in CSV")
        expiry_info_label.setStyleSheet("color: #666; font-size: 11px; font-style: italic;")
        asset_layout.addWidget(expiry_info_label)
        
        parent_layout.addWidget(asset_group, 1)  # Equal stretch factor
    
    def create_right_panel_simplified(self, parent_layout):
        """Create right panel with Archive and Progress sections"""
        # Create splitter for archive and progress sections
        right_splitter = QSplitter(Qt.Vertical)
        
        # === ARCHIVE SECTION ===
        archive_panel = QGroupBox()  # No title (title is now external)
        archive_layout = QVBoxLayout()
        archive_panel.setLayout(archive_layout)
        
        # Main content row: Asset list and Date controls side by side
        main_content_row = QHBoxLayout()
        
        # Asset selection (left side) - just the list
        asset_column = QVBoxLayout()
        self.archive_asset_list = QListWidget()
        # Remove fixed height constraint to allow dynamic resizing
        self.archive_asset_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        asset_column.addWidget(self.archive_asset_list, 1)  # Give it stretch factor to expand
        
        main_content_row.addLayout(asset_column)
        
        # Date controls (right side) - with buttons below dates
        date_column = QVBoxLayout()
        date_column.addWidget(QLabel("Start Date:"))
        self.archive_start_date = QDateEdit()
        self.archive_start_date.setCalendarPopup(True)
        self.archive_start_date.setDate(QDate.currentDate().addDays(-30))  # Default to 30 days ago
        date_column.addWidget(self.archive_start_date)
        
        date_column.addWidget(QLabel("End Date:"))
        self.archive_end_date = QDateEdit()
        self.archive_end_date.setCalendarPopup(True)
        self.archive_end_date.setDate(QDate.currentDate())  # Default to today
        date_column.addWidget(self.archive_end_date)
        
        # Asset control buttons (positioned under date controls)
        asset_btn_row = QHBoxLayout()
        self.select_all_assets_btn = QPushButton("All")
        self.select_all_assets_btn.clicked.connect(self.select_all_archive_assets)
        self.select_all_assets_btn.setMaximumWidth(50)
        asset_btn_row.addWidget(self.select_all_assets_btn)
        
        self.clear_assets_btn = QPushButton("Clear")
        self.clear_assets_btn.clicked.connect(self.clear_archive_assets)
        self.clear_assets_btn.setMaximumWidth(70)  # Increased from 50 to 70
        asset_btn_row.addWidget(self.clear_assets_btn)
        
        asset_btn_row.addStretch()  # Push buttons to left
        date_column.addLayout(asset_btn_row)
        
        date_column.addStretch()  # Fill remaining space
        
        main_content_row.addLayout(date_column)
        archive_layout.addLayout(main_content_row, 1)  # Give main content row stretch factor to expand
        
        # Archive progress bar (hidden by default)
        self.archive_progress = QProgressBar()
        self.archive_progress.setVisible(False)
        archive_layout.addWidget(self.archive_progress)
        
        # Archive status
        self.archive_status_label = QLabel("Ready to create archive")
        self.archive_status_label.setStyleSheet("color: #666; font-size: 11px;")
        archive_layout.addWidget(self.archive_status_label)
        
        right_splitter.addWidget(archive_panel)
        
        # === PROGRESS MONITOR SECTION ===
        progress_panel = QGroupBox()
        progress_layout = QVBoxLayout()
        progress_panel.setLayout(progress_layout)
        
        # Activity Log (reduced height)
        self.status_log = QTextEdit()
        self.status_log.setReadOnly(True)
        self.status_log.setMinimumHeight(150)  # Reduced from 300
        progress_layout.addWidget(self.status_log)
        
        # Current operation
        current_layout = QHBoxLayout()
        current_layout.addWidget(QLabel("Current Operation:"))
        self.current_op_label = QLabel("Ready")
        self.current_op_label.setStyleSheet("font-weight: bold; color: green;")
        current_layout.addWidget(self.current_op_label)
        current_layout.addStretch()
        progress_layout.addLayout(current_layout)
        
        # Progress info
        progress_info_layout = QHBoxLayout()
        self.progress_label = QLabel("Progress: 0/0 assets")
        progress_info_layout.addWidget(self.progress_label)
        
        self.results_label = QLabel("Success: 0 | Failed: 0")
        progress_info_layout.addWidget(self.results_label)
        progress_info_layout.addStretch()
        
        progress_layout.addLayout(progress_info_layout)
        
        # Control buttons
        control_layout = QHBoxLayout()
        self.stop_btn = QPushButton("Stop Collection")
        self.stop_btn.clicked.connect(self.stop_collection_operation)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("QPushButton:enabled { background-color: #dc3545; color: white; }")
        control_layout.addWidget(self.stop_btn)
        
        self.clear_log_btn = QPushButton("Clear Log")
        self.clear_log_btn.clicked.connect(self.clear_status_log)
        control_layout.addWidget(self.clear_log_btn)
        control_layout.addStretch()
        
        progress_layout.addLayout(control_layout)
        
        right_splitter.addWidget(progress_panel)
        
        # Set splitter proportions (archive smaller, progress larger)
        right_splitter.setSizes([180, 320])
        
        parent_layout.addWidget(right_splitter, 1)  # Equal stretch factor
    
    
    def setup_update_timer(self):
        """Setup timer for updating header statistics"""
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_header_stats)
        self.update_timer.start(5000)  # Update every 5 seconds

    def update_header_stats(self):
        """Update header statistics using background thread"""
        # Only start a new worker if one isn't already running
        if not hasattr(self, 'stats_worker') or not self.stats_worker.isRunning():
            self.stats_worker = StatsWorker(self.engine, self.ohlc_engine)
            self.stats_worker.stats_ready.connect(self.on_stats_ready)
            self.stats_worker.stats_error.connect(self.on_stats_error)
            self.stats_worker.start()

    def on_stats_ready(self, engine_stats, ohlc_status, cache_status):
        """Handle stats update from background thread"""
        try:
            # Update OI engine stats
            rate_stats = engine_stats.get('rate_limiting', {})
            success_rate = rate_stats.get('success_rate', 0)

            if success_rate > 80:
                status_color = "green"
                status_symbol = "●"
            elif success_rate > 50:
                status_color = "orange"
                status_symbol = "●"
            else:
                status_color = "red"
                status_symbol = "●"

            self.api_status_label.setText(f"API Status: {status_symbol}")
            self.api_status_label.setStyleSheet(f"color: {status_color};")
            self.success_rate_label.setText(f"Success Rate: {success_rate:.1f}%")

            # Database stats
            db_stats = engine_stats.get('database', {})
            total_records = db_stats.get('total_records', 0)
            self.db_records_label.setText(f"DB Records: {total_records:,}")

            # Rate limiting
            current_delay = rate_stats.get('current_delay', 0)
            self.adaptive_delay_label.setText(f"Adaptive Delay: {current_delay:.1f}s")

            # Last error
            request_stats = engine_stats.get('api_requests', {})
            last_error_time = request_stats.get('last_error_time')
            if last_error_time:
                self.last_error_label.setText(f"Last Error: {last_error_time}")
            else:
                self.last_error_label.setText("Last Error: None")

            # Update OHLC header statistics with pre-fetched data
            self.update_ohlc_header_stats(ohlc_status)

            # Update cache status with pre-fetched data
            self.update_cache_status(cache_status)

        except Exception as e:
            logger.error(f"Error updating header stats: {e}")

    def on_stats_error(self, error_msg):
        """Handle stats update error"""
        logger.error(f"Stats worker error: {error_msg}")
    
    def load_csv_file(self):
        """Browse and load CSV file in one action"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "Select Asset CSV File",
            "",
            "CSV files (*.csv);;All files (*.*)"
        )
        if file_path:
            self.load_assets_from_path(file_path)
    
    def auto_load_assets_csv(self):
        """Automatically load assets_select.csv if it exists in the same directory"""
        assets_csv_path = os.path.join(os.path.dirname(__file__), "assets", "assets_select.csv")
        if os.path.exists(assets_csv_path):
            self.log_status("Auto-loading assets_select.csv...")
            self.load_assets_from_path(assets_csv_path)
            return True
        return False
    
    def load_assets_from_path(self, file_path):
        """Load assets from specified CSV file path"""
        try:
            self.loaded_assets = self.engine.load_assets(file_path)
            if not self.loaded_assets:
                QMessageBox.critical(self, "Error", "Failed to load assets from CSV file")
                self.csv_path_label.setText("Failed to load file")
                self.csv_path_label.setStyleSheet("color: red;")
                return
            
            # Update file path display
            self.csv_file_path = file_path
            filename = os.path.basename(file_path)
            self.csv_path_label.setText(f"File: {filename}")
            self.csv_path_label.setStyleSheet("color: green; font-weight: bold;")
            
            # Update asset list with sorting: IDX_I first, then NSE_EQ, both alphabetical
            self.asset_list.clear()

            # Sort assets: IDX_I first, then NSE_EQ, both alphabetically
            idx_assets = []
            nse_eq_assets = []
            other_assets = []

            for asset in self.loaded_assets:
                if asset.segment_code == 'IDX_I':
                    idx_assets.append(asset)
                elif asset.segment_code == 'NSE_EQ':
                    nse_eq_assets.append(asset)
                else:
                    other_assets.append(asset)

            # Sort each group alphabetically by asset name
            idx_assets.sort(key=lambda x: x.asset_name)
            nse_eq_assets.sort(key=lambda x: x.asset_name)
            other_assets.sort(key=lambda x: x.asset_name)

            # Add to list in order: IDX_I, NSE_EQ, others
            for asset in idx_assets + nse_eq_assets + other_assets:
                self.asset_list.addItem(asset.asset_name)
            
            # Update asset count - simple loaded count
            self.asset_count_label.setText(f"Loaded: {len(self.loaded_assets)} assets")
            
            # Update selection display initially (no assets selected)
            self.update_selection_display()
            
            self.log_status(f"Loaded {len(self.loaded_assets)} assets from {filename}")
            logger.info(f"Loaded {len(self.loaded_assets)} assets from {file_path}")
            
        except Exception as e:
            error_msg = f"Error loading assets: {e}"
            QMessageBox.critical(self, "Error", error_msg)
            self.csv_path_label.setText("Error loading file")
            self.csv_path_label.setStyleSheet("color: red;")
            logger.error(f"Error loading assets from {file_path}: {e}")
    
    def select_all_assets(self):
        """Select all assets in the list"""
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            item.setSelected(True)
    
    def clear_asset_selection(self):
        """Clear asset selection"""
        self.asset_list.clearSelection()
    
    def update_selection_display(self):
        """Update the asset count display based on current selection"""
        if not hasattr(self, 'loaded_assets'):
            return
            
        selected_items = self.asset_list.selectedItems()
        selected_count = len(selected_items)
        
        if selected_count == 0:
            # No selection - show loaded count
            self.asset_count_label.setText(f"Loaded: {len(self.loaded_assets)} assets")
        elif selected_count == 1:
            # Single selection - show asset name and expiry count
            selected_name = selected_items[0].text()
            # Find the asset to get expiry count
            for asset in self.loaded_assets:
                if asset.asset_name == selected_name:
                    self.asset_count_label.setText(f"Selected: {selected_name} ({asset.number_expiries} expiries)")
                    break
        else:
            # Multiple selection - show selected count
            self.asset_count_label.setText(f"Selected: {selected_count} assets")
    
    def collect_multiple_assets(self):
        """Collect data for multiple selected assets"""
        selected_items = self.asset_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Warning", "Please select one or more assets")
            return
        
        selected_assets = []
        for item in selected_items:
            for asset in self.loaded_assets:
                if asset.asset_name == item.text():
                    selected_assets.append(asset)
                    break
        
        self.start_collection_operation(selected_assets)
    
    def start_collection_operation(self, assets_to_collect):
        """Start collection operation in background thread"""
        if hasattr(self, 'collection_thread') and self.collection_thread.isRunning():
            QMessageBox.warning(self, "Warning", "Collection already in progress")
            return
        
        # Reset statistics
        self.collection_stats = {
            'total': len(assets_to_collect),
            'completed': 0,
            'success': 0,
            'failed': 0,
            'current_asset': None
        }
        
        # Update UI
        self.stop_btn.setEnabled(True)
        self.current_op_label.setText("Starting collection...")
        self.update_progress_display()
        
        # Start background thread
        self.collection_thread = CollectionWorker(self.engine, assets_to_collect)
        self.collection_thread.progress.connect(self.on_collection_progress)
        self.collection_thread.asset_complete.connect(self.on_asset_complete)
        self.collection_thread.collection_finished.connect(self.on_collection_finished)
        self.collection_thread.start()
        
        self.log_status(f"Started collection for {len(assets_to_collect)} assets")
    
    def stop_collection_operation(self):
        """Stop ongoing collection operation"""
        if hasattr(self, 'collection_thread'):
            self.collection_thread.stop_requested = True
            self.stop_btn.setEnabled(False)
            self.log_status("Collection stop requested")
    
    def on_collection_progress(self, current_asset):
        """Handle progress update from collection thread"""
        self.collection_stats['current_asset'] = current_asset
        self.current_op_label.setText(f"Collecting: {current_asset}")
    
    def on_asset_complete(self, asset_name, success):
        """Handle asset completion from collection thread"""
        if success:
            self.collection_stats['success'] += 1
            status_msg = f"{asset_name}: Success"
        else:
            self.collection_stats['failed'] += 1
            status_msg = f"{asset_name}: Failed"
        
        self.collection_stats['completed'] += 1
        self.update_progress_display()
        self.log_status(status_msg)

    def on_collection_finished(self, stopped):
        """Handle collection thread completion"""
        self.stop_btn.setEnabled(False)

        if stopped:
            self.current_op_label.setText("Collection Stopped")
            self.log_status("Collection operation stopped")
        else:
            # Capture exact collection completion timestamp
            collection_completion_timestamp = datetime.now().isoformat()

            self.current_op_label.setText("Collection Complete")
            self.log_status("Collection operation completed")
    
    def update_progress_display(self):
        """Update progress display elements"""
        stats = self.collection_stats
        self.progress_label.setText(f"Progress: {stats['completed']}/{stats['total']} assets")
        self.results_label.setText(f"Success: {stats['success']} | Failed: {stats['failed']}")
    
    def log_status(self, message):
        """Add message to status log"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"{timestamp} - {message}"
        self.status_log.append(log_message)
    
    def clear_status_log(self):
        """Clear the status log"""
        self.status_log.clear()
    
    def closeEvent(self, event):
        """Handle application closing"""
        self.update_timer.stop()
        self.schedule_check_timer.stop()

        # Stop collection thread
        if hasattr(self, 'collection_thread') and self.collection_thread.isRunning():
            self.collection_thread.stop_requested = True
            self.collection_thread.wait(2000)  # Wait up to 2 seconds

        # Stop continuous collection thread
        if hasattr(self, 'continuous_collection_thread') and self.continuous_collection_thread and self.continuous_collection_thread.isRunning():
            self.continuous_collection_thread.stop_requested = True
            self.continuous_collection_thread.wait(2000)  # Wait up to 2 seconds

        # Stop archive thread
        if hasattr(self, 'archive_thread') and self.archive_thread.isRunning():
            self.archive_thread.wait(2000)  # Wait up to 2 seconds

        # Stop OHLC engine
        if hasattr(self, 'ohlc_engine'):
            try:
                self.ohlc_engine.stop()
                self.ohlc_engine.cleanup()
                logger.info("OHLC engine stopped during application shutdown")
            except Exception as e:
                logger.error(f"Error stopping OHLC engine: {e}")


        event.accept()

    def refresh_expiry_cache(self):
        """Refresh expiry cache for all assets using background thread"""
        self.refresh_cache_btn.setEnabled(False)
        self.refresh_cache_btn.setText("Refreshing...")
        self.log_status("Starting cache refresh in background...")

        # Start worker thread
        self.cache_refresh_worker = CacheRefreshWorker(self.engine, operation='refresh')
        self.cache_refresh_worker.refresh_complete.connect(self.on_cache_refresh_complete)
        self.cache_refresh_worker.refresh_error.connect(self.on_cache_refresh_error)
        self.cache_refresh_worker.start()

    def on_cache_refresh_complete(self, result):
        """Handle cache refresh completion from background thread"""
        try:
            if 'error' in result:
                QMessageBox.critical(self, "Cache Refresh Error", f"Failed to refresh cache: {result['error']}")
                self.log_status(f"Cache refresh failed: {result['error']}")
            else:
                success_count = result.get('success_count', 0)
                error_count = result.get('error_count', 0)
                total_assets = result.get('total_assets', 0)

                if error_count > 0:
                    errors = result.get('errors', [])[:3]  # Show first 3 errors
                    error_summary = '\n'.join(errors)
                    if len(result.get('errors', [])) > 3:
                        error_summary += f"\n... and {len(result.get('errors', [])) - 3} more"

                    QMessageBox.warning(self, "Cache Refresh Completed with Errors",
                                      f"Refreshed {success_count}/{total_assets} assets successfully.\n\nErrors:\n{error_summary}")
                else:
                    QMessageBox.information(self, "Cache Refresh Complete",
                                          f"Successfully refreshed expiry cache for {success_count} assets.")

                self.log_status(f"Cache refresh completed: {success_count} success, {error_count} errors")
                self.update_cache_status()

        finally:
            self.refresh_cache_btn.setEnabled(True)
            self.refresh_cache_btn.setText("Refresh All Expiries")

    def on_cache_refresh_error(self, error_msg):
        """Handle cache refresh error from background thread"""
        QMessageBox.critical(self, "Error", f"Cache refresh failed: {error_msg}")
        logger.error(f"Cache refresh error: {error_msg}")
        self.refresh_cache_btn.setEnabled(True)
        self.refresh_cache_btn.setText("Refresh All Expiries")
    
    def clear_expiry_cache(self):
        """Clear entire expiry cache using background thread"""
        reply = QMessageBox.question(self, "Clear Cache",
                                   "This will clear all cached expiry dates and force fresh API calls.\n\n"
                                   "Are you sure you want to continue?",
                                   QMessageBox.Yes | QMessageBox.No)

        if reply == QMessageBox.Yes:
            self.clear_cache_btn.setEnabled(False)
            self.clear_cache_btn.setText("Clearing...")
            self.log_status("Starting cache clear in background...")

            # Start worker thread
            self.cache_clear_worker = CacheRefreshWorker(self.engine, operation='clear')
            self.cache_clear_worker.refresh_complete.connect(self.on_cache_clear_complete)
            self.cache_clear_worker.refresh_error.connect(self.on_cache_clear_error)
            self.cache_clear_worker.start()

    def on_cache_clear_complete(self, result):
        """Handle cache clear completion from background thread"""
        try:
            if 'error' in result:
                QMessageBox.critical(self, "Cache Clear Error", f"Failed to clear cache: {result['error']}")
                self.log_status(f"Cache clear failed: {result['error']}")
            else:
                deleted_count = result.get('entries_deleted', 0)
                QMessageBox.information(self, "Cache Cleared",
                                      f"Successfully cleared {deleted_count} cached expiry entries.")
                self.log_status(f"Cache cleared: {deleted_count} entries removed")
                self.update_cache_status()

        finally:
            self.clear_cache_btn.setEnabled(True)
            self.clear_cache_btn.setText("Clear Cache")

    def on_cache_clear_error(self, error_msg):
        """Handle cache clear error from background thread"""
        QMessageBox.critical(self, "Error", f"Cache clear failed: {error_msg}")
        logger.error(f"Cache clear error: {error_msg}")
        self.clear_cache_btn.setEnabled(True)
        self.clear_cache_btn.setText("Clear Cache")
    
    def update_cache_status(self, status=None):
        """Update cache status display"""
        try:
            # Get cache status if not provided
            if status is None:
                status = self.engine.get_expiry_cache_status()

            if 'error' in status:
                self.cache_status_label.setText("Cache: Error getting status")
                self.cache_status_label.setStyleSheet("font-weight: bold; color: red; padding: 5px;")
            else:
                total_entries = status.get('total_entries', 0)
                cache_status = status.get('status', 'unknown')

                if total_entries == 0:
                    self.cache_status_label.setText("Cache: Empty - no entries")
                    self.cache_status_label.setStyleSheet("font-weight: bold; color: orange; padding: 5px;")
                else:
                    expires_tuesday = status.get('expires_tuesday_4pm', '')
                    if expires_tuesday:
                        from datetime import datetime
                        try:
                            expires_dt = datetime.fromisoformat(expires_tuesday.replace('Z', '+00:00'))
                            expires_str = expires_dt.strftime("%A %b %d, %Y at %I:%M %p")
                        except:
                            expires_str = "Next Tuesday 4 PM"
                    else:
                        expires_str = "Next Tuesday 4 PM"

                    self.cache_status_label.setText(f"Cache: {total_entries} assets, expires {expires_str}")
                    self.cache_status_label.setStyleSheet("font-weight: bold; color: green; padding: 5px;")

        except Exception as e:
            self.cache_status_label.setText("Cache: Error updating status")
            self.cache_status_label.setStyleSheet("font-weight: bold; color: red; padding: 5px;")
            logger.error(f"Error updating cache status: {e}")

    # ===== SCHEDULING HELPER METHODS =====
    
    def populate_time_combo(self, combo):
        """Populate time combo with 15-minute intervals"""
        combo.clear()
        for hour in range(24):
            for minute in [0, 15, 30, 45]:
                time_str = f"{hour:02d}:{minute:02d}"
                combo.addItem(time_str)
    
    def set_combo_time(self, combo, time_str):
        """Set combo to specific time"""
        index = combo.findText(time_str)
        if index >= 0:
            combo.setCurrentIndex(index)
    
    def set_frequency_combo(self):
        """Set frequency combo based on current schedule type"""
        current_type = self.schedule_manager.schedule_config['schedule_type']
        if current_type == 'daily':
            self.frequency_combo.setCurrentText("Daily")
        elif current_type == 'weekdays':
            self.frequency_combo.setCurrentText("Weekdays")
        else:
            self.frequency_combo.setCurrentText("Weekdays")  # Default
    
    def set_schedule_date(self):
        """Set schedule date from configuration (deprecated - no longer used)"""
        pass  # Date picker removed
    
    # ===== SCHEDULING METHODS =====
    
    def on_schedule_enabled_changed(self, state):
        """Handle schedule enable/disable checkbox change"""
        enabled = state == 2  # Qt.Checked
        self.save_current_schedule_settings()
        logger.info(f"Schedule enabled: {enabled}")
    
    def on_schedule_time_changed(self):
        """Handle start/end time changes"""
        self.save_current_schedule_settings()
    
    def on_schedule_type_changed(self):
        """Handle frequency combo changes"""
        self.save_current_schedule_settings()
    
    def save_current_schedule_settings(self):
        """Save current GUI schedule settings to ScheduleManager"""
        try:
            enabled = self.schedule_checkbox.isChecked()
            start_time = self.start_time_combo.currentText()
            end_time = self.end_time_combo.currentText()

            # Determine schedule type from frequency combo
            frequency_text = self.frequency_combo.currentText()
            if frequency_text == "Daily":
                schedule_type = 'daily'
            elif frequency_text == "Weekdays":
                schedule_type = 'weekdays'
            else:
                schedule_type = 'weekdays'  # Default fallback

            # Update schedule manager (no date parameter needed)
            self.schedule_manager.update_schedule(
                enabled, start_time, end_time, schedule_type, None
            )

        except Exception as e:
            logger.error(f"Error saving schedule settings: {e}")
    
    def check_schedule_trigger(self):
        """Check if scheduled collection should be triggered (called by timer)"""
        try:
            # Check for auto-start conditions
            if not hasattr(self, 'continuous_collection_thread') or not self.continuous_collection_thread:
                # No continuous collection running
                if self.schedule_manager.schedule_config['enabled'] and self.schedule_manager.should_start_collection():
                    # Assets must be loaded
                    if self.loaded_assets:
                        logger.info("Schedule trigger detected - starting continuous collection")
                        self.start_continuous_collection()
                    else:
                        logger.warning("Schedule trigger detected but no assets loaded")
            
        except Exception as e:
            logger.error(f"Error in schedule trigger check: {e}")
    
    def start_continuous_collection(self):
        """Start continuous scheduled collection"""
        try:
            # Validation checks
            if not self.loaded_assets:
                QMessageBox.warning(self, "No Assets", "Please load assets from CSV first")
                return
            
            # Check if already running
            if (hasattr(self, 'continuous_collection_thread') and 
                self.continuous_collection_thread and 
                self.continuous_collection_thread.isRunning()):
                QMessageBox.warning(self, "Already Running", "Continuous collection is already running")
                return
            
            # Check for conflicts with regular collection
            if (hasattr(self, 'collection_thread') and 
                self.collection_thread and 
                self.collection_thread.isRunning()):
                reply = QMessageBox.question(
                    self, "Collection Conflict", 
                    "Regular collection is running. Stop it and start continuous collection?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.stop_collection_operation()
                    # Wait a moment for cleanup
                    QApplication.processEvents()
                else:
                    return
            
            # Reset collection stats for continuous mode
            self.collection_stats = {
                'total': len(self.loaded_assets),
                'completed': 0,
                'success': 0,
                'failed': 0,
                'current_asset': None,
                'current_loop': 0,
                'total_collections': 0
            }
            
            # Update UI
            self.start_schedule_btn.setEnabled(False)
            self.stop_schedule_btn.setEnabled(True)
            self.current_op_label.setText("Starting continuous collection...")
            
            # Start continuous collection thread
            self.continuous_collection_thread = ContinuousCollectionWorker(
                self.engine, self.loaded_assets, self.schedule_manager
            )
            
            # Connect signals
            self.continuous_collection_thread.progress.connect(self.on_continuous_progress)
            self.continuous_collection_thread.asset_complete.connect(self.on_continuous_asset_complete)
            self.continuous_collection_thread.loop_complete.connect(self.on_continuous_loop_complete)
            self.continuous_collection_thread.collection_finished.connect(self.on_continuous_finished)
            self.continuous_collection_thread.schedule_status_changed.connect(self.on_continuous_status_changed)
            
            self.continuous_collection_thread.start()
            
            self.log_status(f"Started continuous collection for {len(self.loaded_assets)} assets")
            logger.info("Continuous collection started")
            
        except Exception as e:
            error_msg = f"Failed to start continuous collection: {e}"
            QMessageBox.critical(self, "Error", error_msg)
            logger.error(error_msg)
            
            # Reset UI on error
            self.start_schedule_btn.setEnabled(True)
            self.stop_schedule_btn.setEnabled(False)
    
    def stop_continuous_collection(self):
        """Stop continuous scheduled collection"""
        try:
            if hasattr(self, 'continuous_collection_thread') and self.continuous_collection_thread:
                self.continuous_collection_thread.stop_requested = True
                self.log_status("Continuous collection stop requested")
                logger.info("Continuous collection stop requested")
                
                # Disable stop button immediately
                self.stop_schedule_btn.setEnabled(False)
                
        except Exception as e:
            logger.error(f"Error stopping continuous collection: {e}")
    
    def on_continuous_progress(self, progress_msg):
        """Handle progress update from continuous collection"""
        self.current_op_label.setText(progress_msg)
    
    def on_continuous_asset_complete(self, asset_name, success):
        """Handle asset completion from continuous collection"""
        if success:
            self.collection_stats['success'] += 1
            status_msg = f"{asset_name}: Success"
        else:
            self.collection_stats['failed'] += 1
            status_msg = f"{asset_name}: Failed"
        
        self.collection_stats['completed'] += 1
        self.collection_stats['total_collections'] = self.collection_stats.get('total_collections', 0) + 1
        
        # Update display
        stats = self.collection_stats
        self.progress_label.setText(f"Loop {stats.get('current_loop', 0)} - Total Collections: {stats['total_collections']}")
        self.results_label.setText(f"Success: {stats['success']} | Failed: {stats['failed']}")
        
        # Log occasionally to avoid spam
        if self.collection_stats['total_collections'] % 10 == 0:
            self.log_status(f"Continuous: {self.collection_stats['total_collections']} collections completed")
    
    def on_continuous_loop_complete(self, loop_number):
        """Handle loop completion from continuous collection"""
        self.collection_stats['current_loop'] = loop_number
        self.collection_stats['completed'] = 0  # Reset per-loop counter

        # Capture exact loop completion timestamp
        loop_completion_timestamp = datetime.now().isoformat()

        self.log_status(f"Completed collection loop {loop_number}")
        logger.info(f"Continuous collection loop {loop_number} completed")
    
    def on_continuous_status_changed(self, status_msg):
        """Handle status change from continuous collection"""
        self.current_op_label.setText(status_msg)
        if "window ended" in status_msg.lower():
            self.log_status("Collection window ended - continuous collection stopping")
    
    def on_continuous_finished(self, stopped):
        """Handle continuous collection completion"""
        try:
            # Reset UI
            self.start_schedule_btn.setEnabled(True)
            self.stop_schedule_btn.setEnabled(False)
            
            if stopped:
                self.current_op_label.setText("Continuous Collection Stopped")
                self.log_status("Continuous collection stopped by user")
            else:
                self.current_op_label.setText("Continuous Collection Complete")
                self.log_status("Continuous collection completed (schedule window ended)")
            
            # Show completion stats
            if hasattr(self, 'continuous_collection_thread') and self.continuous_collection_thread:
                stats = self.continuous_collection_thread.get_stats()
                total_loops = stats.get('current_loop', 0)
                total_collections = stats.get('total_collections', 0)
                
                self.log_status(f"Final stats: {total_loops} loops, {total_collections} total collections")
                logger.info(f"Continuous collection finished: {total_loops} loops, {total_collections} collections")
            
            # Clear thread reference
            self.continuous_collection_thread = None
            
        except Exception as e:
            logger.error(f"Error handling continuous collection finish: {e}")
    
    # ===== END SCHEDULING METHODS =====
    
    # ===== ARCHIVE METHODS =====
    
    def populate_archive_assets(self):
        """Populate archive asset list with available assets from database"""
        try:
            available_assets = self.archiver.get_available_assets()
            self.archive_asset_list.clear()
            
            # Store the mapping for later use
            self.archive_asset_mapping = {}
            
            # Sort assets: IDX_I first, then alphabetical
            idx_assets = []
            other_assets = []
            
            for asset_info in available_assets:
                display_name = asset_info['display_name']
                underlying_symbol = asset_info['underlying_symbol']
                
                # Store mapping for conversion back
                self.archive_asset_mapping[display_name] = underlying_symbol
                
                # Separate IDX_I assets from others
                if 'IDX_I' in underlying_symbol:
                    idx_assets.append(display_name)
                else:
                    other_assets.append(display_name)
            
            # Sort each group alphabetically
            idx_assets.sort()
            other_assets.sort()
            
            # Add IDX_I assets first, then others
            for display_name in idx_assets:
                self.archive_asset_list.addItem(display_name)
            
            for display_name in other_assets:
                self.archive_asset_list.addItem(display_name)
            
            # Update status
            if available_assets:
                idx_count = len(idx_assets)
                other_count = len(other_assets)
                status_msg = f"{len(available_assets)} assets available ({idx_count} indices, {other_count} stocks)"
                self.archive_status_label.setText(status_msg)
            else:
                self.archive_status_label.setText("No assets found in database")
                
        except Exception as e:
            logger.error(f"Error populating archive assets: {e}")
            self.archive_status_label.setText("Error loading available assets")
    
    def select_all_archive_assets(self):
        """Select all assets in archive list"""
        for i in range(self.archive_asset_list.count()):
            item = self.archive_asset_list.item(i)
            item.setSelected(True)
    
    def clear_archive_assets(self):
        """Clear archive asset selection"""
        self.archive_asset_list.clearSelection()
    
    def create_database_archive(self):
        """Create database archive with selected criteria"""
        try:
            # Validate inputs
            start_date_qdate = self.archive_start_date.date()
            end_date_qdate = self.archive_end_date.date()
            
            if start_date_qdate > end_date_qdate:
                QMessageBox.warning(self, "Invalid Date Range", "Start date cannot be after end date")
                return
            
            start_date_str = start_date_qdate.toString("yyyy-MM-dd")
            end_date_str = end_date_qdate.toString("yyyy-MM-dd")
            
            # Get selected assets and convert display names to underlying symbols
            selected_items = self.archive_asset_list.selectedItems()
            if selected_items:
                # Convert display names back to underlying symbols
                selected_assets = []
                for item in selected_items:
                    display_name = item.text()
                    underlying_symbol = self.archive_asset_mapping.get(display_name)
                    if underlying_symbol:
                        selected_assets.append(underlying_symbol)
                
                if not selected_assets:
                    selected_assets = None  # Fallback to all assets
            else:
                selected_assets = None
            
            # Check if any criteria selected
            if not selected_assets:
                reply = QMessageBox.question(
                    self, "No Assets Selected", 
                    "No assets selected. This will archive ALL assets for the date range. Continue?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            
            # Generate default filename
            default_filename = self.archiver.generate_default_filename(
                selected_assets, start_date_str, end_date_str
            )
            
            # Get save location from user
            archive_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Archive As",
                default_filename,
                "SQLite Database (*.db);;All files (*.*)"
            )
            
            if not archive_path:
                return  # User cancelled
            
            # Start archive creation in background thread
            self.start_archive_operation(archive_path, selected_assets, start_date_str, end_date_str)
            
        except Exception as e:
            error_msg = f"Error creating archive: {e}"
            QMessageBox.critical(self, "Archive Error", error_msg)
            logger.error(error_msg)
    
    def start_archive_operation(self, archive_path: str, assets: Optional[List[str]], 
                               start_date: str, end_date: str):
        """Start archive operation in background thread"""
        if hasattr(self, 'archive_thread') and self.archive_thread.isRunning():
            QMessageBox.warning(self, "Archive In Progress", "Archive creation already in progress")
            return
        
        # Update UI
        self.create_archive_header_btn.setEnabled(False)
        self.archive_progress.setVisible(True)
        self.archive_progress.setValue(0)
        self.archive_status_label.setText("Creating archive...")
        
        # Start background thread
        self.archive_thread = ArchiveWorker(
            self.archiver, archive_path, assets, start_date, end_date
        )
        self.archive_thread.progress_update.connect(self.on_archive_progress)
        self.archive_thread.archive_complete.connect(self.on_archive_complete)
        self.archive_thread.archive_error.connect(self.on_archive_error)
        self.archive_thread.start()
        
        self.log_status(f"Started archive creation: {os.path.basename(archive_path)}")
    
    def on_archive_progress(self, message: str, percentage: int):
        """Handle archive progress updates"""
        self.archive_status_label.setText(message)
        self.archive_progress.setValue(percentage)
        
        # Also log significant progress milestones
        if percentage in [25, 50, 75, 100]:
            self.log_status(f"Archive: {message}")
    
    def on_archive_complete(self, archive_stats: ArchiveStats, archive_path: str):
        """Handle archive completion"""
        try:
            # Reset UI
            self.create_archive_header_btn.setEnabled(True)
            self.archive_progress.setVisible(False)
            self.archive_status_label.setText("Archive created successfully")
            
            # Show completion dialog
            completion_msg = (
                f"Archive created successfully!\n\n"
                f"Location: {archive_path}\n"
                f"Total Records: {archive_stats.total_records:,}\n"
                f"  • Options Data: {archive_stats.options_data_records:,}\n"
                f"  • Asset Metadata: {archive_stats.asset_metadata_records:,}\n"
                f"  • Collection Status: {archive_stats.collection_status_records:,}\n"
                f"  • Expiry Cache: {archive_stats.expiry_cache_records:,}\n"
                f"Archive Size: {archive_stats.archive_size_mb:.2f} MB\n\n"
                f"Open containing folder?"
            )
            
            reply = QMessageBox.question(
                self, "Archive Complete", completion_msg,
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # Open folder containing the archive
                folder_path = os.path.dirname(archive_path)
                if sys.platform == "darwin":  # macOS
                    subprocess.run(["open", folder_path])
                elif sys.platform == "win32":  # Windows
                    subprocess.run(["explorer", folder_path])
                else:  # Linux
                    subprocess.run(["xdg-open", folder_path])
            
            self.log_status(f"Archive completed: {archive_stats.total_records:,} records, {archive_stats.archive_size_mb:.2f} MB")
            
        except Exception as e:
            logger.error(f"Error handling archive completion: {e}")
    
    def on_archive_error(self, error_message: str):
        """Handle archive errors"""
        # Reset UI
        self.create_archive_header_btn.setEnabled(True)
        self.archive_progress.setVisible(False)
        self.archive_status_label.setText("Archive creation failed")
        
        # Show error dialog
        QMessageBox.critical(self, "Archive Error", f"Archive creation failed:\n\n{error_message}")
        
        self.log_status(f"Archive failed: {error_message}")
    
    # ===== END ARCHIVE METHODS =====

    # ===== OHLC ENGINE METHODS =====

    def ohlc_header_panel(self, parent_layout):
        """Create OHLC header panel"""
        header_frame = QFrame()
        header_frame.setFrameStyle(QFrame.Box | QFrame.Raised)
        header_frame.setLineWidth(1)
        header_frame.setFixedHeight(80)

        header_layout = QVBoxLayout()
        header_frame.setLayout(header_layout)

        # Top row
        top_row = QHBoxLayout()

        self.ohlc_api_status_label = QLabel("OHLC API Status: ●")
        self.ohlc_api_status_label.setStyleSheet("color: green;")
        top_row.addWidget(self.ohlc_api_status_label)

        top_row.addWidget(QLabel(" | "))

        self.ohlc_refresh_rate_label = QLabel("Refresh Rate: 5s")
        top_row.addWidget(self.ohlc_refresh_rate_label)

        top_row.addWidget(QLabel(" | "))

        self.ohlc_db_records_label = QLabel("OHLC Records: 0")
        top_row.addWidget(self.ohlc_db_records_label)

        top_row.addStretch()

        # OHLC specific buttons
        self.create_ohlc_archive_btn = QPushButton("Create Archive")
        self.create_ohlc_archive_btn.clicked.connect(self.create_ohlc_archive)
        self.create_ohlc_archive_btn.setMinimumHeight(35)
        self.create_ohlc_archive_btn.setStyleSheet("QPushButton { background-color: #28a745; color: white; font-weight: bold; }")
        top_row.addWidget(self.create_ohlc_archive_btn)

        self.load_ohlc_csv_btn = QPushButton("Load CSV")
        self.load_ohlc_csv_btn.clicked.connect(self.load_ohlc_csv_file)
        self.load_ohlc_csv_btn.setMinimumHeight(35)
        self.load_ohlc_csv_btn.setStyleSheet("QPushButton { background-color: #007bff; color: white; font-weight: bold; }")
        top_row.addWidget(self.load_ohlc_csv_btn)

        header_layout.addLayout(top_row)

        # Bottom row
        bottom_row = QHBoxLayout()

        self.ohlc_last_fetch_label = QLabel("Last Fetch: Never")
        bottom_row.addWidget(self.ohlc_last_fetch_label)

        bottom_row.addWidget(QLabel(" | "))

        self.ohlc_engine_status_label = QLabel("Engine: Stopped")
        bottom_row.addWidget(self.ohlc_engine_status_label)

        bottom_row.addStretch()
        header_layout.addLayout(bottom_row)

        parent_layout.addWidget(header_frame)

    def ohlc_asset_panel(self, parent_layout):
        """Create OHLC asset management panel"""
        asset_group = QGroupBox()

        asset_layout = QVBoxLayout()
        asset_group.setLayout(asset_layout)

        # OHLC Asset list
        self.ohlc_asset_list = QListWidget()
        self.ohlc_asset_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ohlc_asset_list.setMaximumHeight(300)
        asset_layout.addWidget(self.ohlc_asset_list)

        # File path and asset count
        file_info_layout = QHBoxLayout()

        self.ohlc_csv_path_label = QLabel("No OHLC file selected")
        self.ohlc_csv_path_label.setStyleSheet("color: gray;")
        file_info_layout.addWidget(self.ohlc_csv_path_label)

        file_info_layout.addStretch()

        self.ohlc_asset_count_label = QLabel("Loaded: 0 assets")
        file_info_layout.addWidget(self.ohlc_asset_count_label)

        asset_layout.addLayout(file_info_layout)

        # Control buttons
        buttons_layout = QHBoxLayout()

        self.start_ohlc_btn = QPushButton("Start OHLC")
        self.start_ohlc_btn.clicked.connect(self.start_ohlc_collection)
        self.start_ohlc_btn.setStyleSheet("QPushButton { background-color: #28a745; color: white; font-weight: bold; }")
        buttons_layout.addWidget(self.start_ohlc_btn)

        self.stop_ohlc_btn = QPushButton("Stop OHLC")
        self.stop_ohlc_btn.clicked.connect(self.stop_ohlc_collection)
        self.stop_ohlc_btn.setEnabled(False)
        self.stop_ohlc_btn.setStyleSheet("QPushButton:enabled { background-color: #dc3545; color: white; font-weight: bold; }")
        buttons_layout.addWidget(self.stop_ohlc_btn)

        # OHLC Schedule checkbox
        self.ohlc_schedule_checkbox = QCheckBox("Schedule")
        self.ohlc_schedule_checkbox.setChecked(self.ohlc_schedule_manager.schedule_config['enabled'])
        self.ohlc_schedule_checkbox.stateChanged.connect(self.on_ohlc_schedule_enabled_changed)
        buttons_layout.addWidget(self.ohlc_schedule_checkbox)

        buttons_layout.addStretch()
        asset_layout.addLayout(buttons_layout)

        # OHLC Schedule controls
        schedule_controls_layout = QHBoxLayout()

        # Left column: Start and End time dropdowns
        left_column = QVBoxLayout()

        self.ohlc_start_time_combo = QComboBox()
        self.populate_time_combo(self.ohlc_start_time_combo)
        self.set_combo_time(self.ohlc_start_time_combo, self.ohlc_schedule_manager.schedule_config['start_time'])
        self.ohlc_start_time_combo.currentTextChanged.connect(self.on_ohlc_schedule_time_changed)
        left_column.addWidget(QLabel("Start Time:"))
        left_column.addWidget(self.ohlc_start_time_combo)

        self.ohlc_end_time_combo = QComboBox()
        self.populate_time_combo(self.ohlc_end_time_combo)
        self.set_combo_time(self.ohlc_end_time_combo, self.ohlc_schedule_manager.schedule_config['end_time'])
        self.ohlc_end_time_combo.currentTextChanged.connect(self.on_ohlc_schedule_time_changed)
        left_column.addWidget(QLabel("End Time:"))
        left_column.addWidget(self.ohlc_end_time_combo)

        # Right column: Frequency dropdown
        right_column = QVBoxLayout()

        self.ohlc_frequency_combo = QComboBox()
        self.ohlc_frequency_combo.addItems(["Daily", "Weekdays"])
        self.set_ohlc_frequency_combo()
        self.ohlc_frequency_combo.currentTextChanged.connect(self.on_ohlc_schedule_type_changed)
        right_column.addWidget(QLabel("Frequency:"))
        right_column.addWidget(self.ohlc_frequency_combo)

        right_column.addStretch()  # Fill remaining space

        schedule_controls_layout.addLayout(left_column)
        schedule_controls_layout.addLayout(right_column)
        asset_layout.addLayout(schedule_controls_layout)

        # Refresh rate controls
        rate_layout = QHBoxLayout()

        rate_layout.addWidget(QLabel("Refresh Rate:"))

        self.ohlc_refresh_combo = QComboBox()
        self.ohlc_refresh_combo.addItems(["1 second", "5 seconds", "10 seconds", "15 seconds", "30 seconds", "60 seconds"])
        self.ohlc_refresh_combo.setCurrentText("5 seconds")
        rate_layout.addWidget(self.ohlc_refresh_combo)

        self.apply_rate_btn = QPushButton("Apply")
        self.apply_rate_btn.clicked.connect(self.apply_refresh_rate)
        rate_layout.addWidget(self.apply_rate_btn)

        rate_layout.addStretch()
        asset_layout.addLayout(rate_layout)

        parent_layout.addWidget(asset_group, 1)

    def ohlc_activity_panel(self, parent_layout):
        """Create OHLC activity panel with archive selection and activity log"""
        # Create splitter for archive and activity sections
        right_splitter = QSplitter(Qt.Vertical)

        # === OHLC ARCHIVE SECTION ===
        archive_panel = QGroupBox()
        archive_layout = QVBoxLayout()
        archive_panel.setLayout(archive_layout)

        # Main content row: Asset list and Date controls side by side
        main_content_row = QHBoxLayout()

        # Asset selection (left side)
        asset_column = QVBoxLayout()
        self.ohlc_archive_asset_list = QListWidget()
        self.ohlc_archive_asset_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        asset_column.addWidget(self.ohlc_archive_asset_list, 1)

        main_content_row.addLayout(asset_column)

        # Date controls (right side)
        date_column = QVBoxLayout()
        date_column.addWidget(QLabel("Start Date:"))
        self.ohlc_archive_start_date = QDateEdit()
        self.ohlc_archive_start_date.setCalendarPopup(True)
        self.ohlc_archive_start_date.setDate(QDate.currentDate().addDays(-30))
        date_column.addWidget(self.ohlc_archive_start_date)

        date_column.addWidget(QLabel("End Date:"))
        self.ohlc_archive_end_date = QDateEdit()
        self.ohlc_archive_end_date.setCalendarPopup(True)
        self.ohlc_archive_end_date.setDate(QDate.currentDate())
        date_column.addWidget(self.ohlc_archive_end_date)

        # Asset control buttons
        asset_btn_row = QHBoxLayout()
        self.select_all_ohlc_assets_btn = QPushButton("All")
        self.select_all_ohlc_assets_btn.clicked.connect(self.select_all_ohlc_archive_assets)
        self.select_all_ohlc_assets_btn.setMaximumWidth(50)
        asset_btn_row.addWidget(self.select_all_ohlc_assets_btn)

        self.clear_ohlc_assets_btn = QPushButton("Clear")
        self.clear_ohlc_assets_btn.clicked.connect(self.clear_ohlc_archive_assets)
        self.clear_ohlc_assets_btn.setMaximumWidth(70)
        asset_btn_row.addWidget(self.clear_ohlc_assets_btn)

        asset_btn_row.addStretch()
        date_column.addLayout(asset_btn_row)
        date_column.addStretch()

        main_content_row.addLayout(date_column)
        archive_layout.addLayout(main_content_row, 1)

        # Archive progress bar (hidden by default)
        self.ohlc_archive_progress = QProgressBar()
        self.ohlc_archive_progress.setVisible(False)
        archive_layout.addWidget(self.ohlc_archive_progress)

        # Archive status
        self.ohlc_archive_status_label = QLabel("Ready to create OHLC archive")
        self.ohlc_archive_status_label.setStyleSheet("color: #666; font-size: 11px;")
        archive_layout.addWidget(self.ohlc_archive_status_label)

        right_splitter.addWidget(archive_panel)

        # === OHLC ACTIVITY MONITOR SECTION ===
        activity_panel = QGroupBox()
        activity_layout = QVBoxLayout()
        activity_panel.setLayout(activity_layout)

        # Activity Log (reduced height)
        self.ohlc_status_log = QTextEdit()
        self.ohlc_status_log.setReadOnly(True)
        self.ohlc_status_log.setMinimumHeight(150)
        activity_layout.addWidget(self.ohlc_status_log)

        # Current operation
        current_layout = QHBoxLayout()
        current_layout.addWidget(QLabel("OHLC Status:"))
        self.ohlc_current_op_label = QLabel("Ready")
        self.ohlc_current_op_label.setStyleSheet("font-weight: bold; color: green;")
        current_layout.addWidget(self.ohlc_current_op_label)
        current_layout.addStretch()
        activity_layout.addLayout(current_layout)

        # Stats
        stats_layout = QHBoxLayout()
        self.ohlc_fetch_count_label = QLabel("Fetches: 0")
        stats_layout.addWidget(self.ohlc_fetch_count_label)

        self.ohlc_success_rate_label = QLabel("Success: 0%")
        stats_layout.addWidget(self.ohlc_success_rate_label)
        stats_layout.addStretch()

        activity_layout.addLayout(stats_layout)

        # Control buttons
        control_layout = QHBoxLayout()
        self.clear_ohlc_log_btn = QPushButton("Clear Log")
        self.clear_ohlc_log_btn.clicked.connect(self.clear_ohlc_status_log)
        control_layout.addWidget(self.clear_ohlc_log_btn)
        control_layout.addStretch()

        activity_layout.addLayout(control_layout)

        right_splitter.addWidget(activity_panel)

        parent_layout.addWidget(right_splitter, 1)

    # OHLC Engine control methods (stubs for now)
    def load_ohlc_csv_file(self):
        """Load OHLC assets CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select OHLC Asset CSV File",
            "assets/assets_select.csv",
            "CSV files (*.csv);;All files (*.*)"
        )
        if file_path:
            self.load_ohlc_assets_from_path(file_path)

    def auto_load_ohlc_assets(self):
        """Auto-load OHLC assets that were already loaded by the engine"""
        try:
            # Get assets that were already loaded by OHLC engine during initialization
            self.ohlc_loaded_assets = self.ohlc_engine.get_loaded_assets()

            if self.ohlc_loaded_assets:
                # Update OHLC asset list in GUI
                self.ohlc_asset_list.clear()

                # Sort assets: IDX_I first, then NSE_EQ, both alphabetically
                idx_assets = []
                nse_eq_assets = []
                other_assets = []

                for asset in self.ohlc_loaded_assets:
                    if asset['segment_code'] == 'IDX_I':
                        idx_assets.append(asset)
                    elif asset['segment_code'] == 'NSE_EQ':
                        nse_eq_assets.append(asset)
                    else:
                        other_assets.append(asset)

                # Sort each group alphabetically by asset name
                idx_assets.sort(key=lambda x: x['asset_name'])
                nse_eq_assets.sort(key=lambda x: x['asset_name'])
                other_assets.sort(key=lambda x: x['asset_name'])

                # Add to list in order: IDX_I, NSE_EQ, others
                for asset in idx_assets + nse_eq_assets + other_assets:
                    self.ohlc_asset_list.addItem(asset['asset_name'])

                # Update OHLC archive asset list
                self.populate_ohlc_archive_assets()

                # Update file path and count display
                self.ohlc_csv_file_path = "assets/assets_select.csv"
                self.ohlc_csv_path_label.setText("File: assets_select.csv")
                self.ohlc_csv_path_label.setStyleSheet("color: green; font-weight: bold;")
                self.ohlc_asset_count_label.setText(f"Loaded: {len(self.ohlc_loaded_assets)} assets")

                self.log_ohlc_status(f"Auto-loaded {len(self.ohlc_loaded_assets)} OHLC assets from assets_select.csv")
                logger.info(f"Auto-loaded {len(self.ohlc_loaded_assets)} OHLC assets")
                return True
            else:
                self.log_ohlc_status("No OHLC assets were loaded")
                return False

        except Exception as e:
            self.log_ohlc_status(f"Error auto-loading OHLC assets: {e}")
            logger.error(f"Error auto-loading OHLC assets: {e}")
            return False

    def load_ohlc_assets_from_path(self, file_path):
        """Load OHLC assets from custom CSV file"""
        try:
            # Use the OHLC engine's load_assets_from_csv method
            if self.ohlc_engine.load_assets_from_csv(file_path):
                # Get the newly loaded assets
                self.ohlc_loaded_assets = self.ohlc_engine.get_loaded_assets()

                # Update GUI
                self.ohlc_asset_list.clear()

                # Sort assets: IDX_I first, then NSE_EQ, both alphabetically
                idx_assets = []
                nse_eq_assets = []
                other_assets = []

                for asset in self.ohlc_loaded_assets:
                    if asset['segment_code'] == 'IDX_I':
                        idx_assets.append(asset)
                    elif asset['segment_code'] == 'NSE_EQ':
                        nse_eq_assets.append(asset)
                    else:
                        other_assets.append(asset)

                # Sort each group alphabetically by asset name
                idx_assets.sort(key=lambda x: x['asset_name'])
                nse_eq_assets.sort(key=lambda x: x['asset_name'])
                other_assets.sort(key=lambda x: x['asset_name'])

                # Add to list in order: IDX_I, NSE_EQ, others
                for asset in idx_assets + nse_eq_assets + other_assets:
                    self.ohlc_asset_list.addItem(asset['asset_name'])

                # Update file path and count display
                self.ohlc_csv_file_path = file_path
                filename = os.path.basename(file_path)
                self.ohlc_csv_path_label.setText(f"File: {filename}")
                self.ohlc_csv_path_label.setStyleSheet("color: green; font-weight: bold;")
                self.ohlc_asset_count_label.setText(f"Loaded: {len(self.ohlc_loaded_assets)} assets")

                self.log_ohlc_status(f"Loaded {len(self.ohlc_loaded_assets)} OHLC assets from {filename}")
                return True
            else:
                # Handle load failure
                self.ohlc_csv_path_label.setText("Failed to load file")
                self.ohlc_csv_path_label.setStyleSheet("color: red;")
                self.log_ohlc_status(f"Failed to load OHLC assets from {file_path}")
                return False

        except Exception as e:
            self.ohlc_csv_path_label.setText("Error loading file")
            self.ohlc_csv_path_label.setStyleSheet("color: red;")
            self.log_ohlc_status(f"Error loading OHLC assets: {e}")
            logger.error(f"Error loading OHLC assets from {file_path}: {e}")
            return False

    def start_ohlc_collection(self):
        """Start OHLC data collection using real engine"""
        try:
            if not self.ohlc_loaded_assets:
                QMessageBox.warning(self, "No Assets", "Please load OHLC assets first")
                return

            # Check if already running
            status = self.ohlc_engine.get_engine_status()
            if status['running']:
                QMessageBox.warning(self, "Already Running", "OHLC collection is already running")
                return

            # Start the engine
            if self.ohlc_engine.start():
                self.start_ohlc_btn.setEnabled(False)
                self.stop_ohlc_btn.setEnabled(True)
                self.ohlc_current_op_label.setText("OHLC collection running...")
                self.log_ohlc_status("OHLC collection started successfully")
                logger.info("OHLC collection started from GUI")
            else:
                QMessageBox.critical(self, "Start Failed", "Failed to start OHLC collection")
                self.log_ohlc_status("Failed to start OHLC collection")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error starting OHLC collection: {e}")
            self.log_ohlc_status(f"Error starting OHLC collection: {e}")
            logger.error(f"Error starting OHLC collection: {e}")

    def stop_ohlc_collection(self):
        """Stop OHLC data collection using real engine"""
        try:
            # Stop the engine
            self.ohlc_engine.stop()

            self.start_ohlc_btn.setEnabled(True)
            self.stop_ohlc_btn.setEnabled(False)
            self.ohlc_current_op_label.setText("Stopped")
            self.log_ohlc_status("OHLC collection stopped")
            logger.info("OHLC collection stopped from GUI")

        except Exception as e:
            self.log_ohlc_status(f"Error stopping OHLC collection: {e}")
            logger.error(f"Error stopping OHLC collection: {e}")

    def apply_refresh_rate(self):
        """Apply new refresh rate using engine's dynamic control"""
        rate_text = self.ohlc_refresh_combo.currentText()

        # Extract seconds from text (e.g., "5 seconds" -> 5)
        try:
            rate_seconds = int(rate_text.split()[0])

            # Get current engine status
            status = self.ohlc_engine.get_engine_status()
            is_running = status['running']

            if is_running:
                # Show restart confirmation if engine is running
                reply = QMessageBox.question(
                    self, "Refresh Rate Change",
                    f"Changing refresh rate to {rate_seconds} seconds while engine is running.\n\n"
                    f"The change will take effect after restarting OHLC collection. Continue?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return

            # Apply the new refresh rate
            if self.ohlc_engine.set_refresh_interval(rate_seconds):
                self.log_ohlc_status(f"Refresh rate changed to {rate_seconds}s")

                if is_running:
                    self.log_ohlc_status("Restart OHLC collection for change to take effect")
                    # Offer to restart automatically
                    restart_reply = QMessageBox.question(
                        self, "Auto Restart",
                        "Restart OHLC collection now to apply the new refresh rate?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if restart_reply == QMessageBox.Yes:
                        self.stop_ohlc_collection()
                        # Brief delay for cleanup using non-blocking timer
                        QApplication.processEvents()
                        QTimer.singleShot(500, self.start_ohlc_collection)
                else:
                    self.log_ohlc_status("New refresh rate will be used when OHLC collection starts")

            else:
                self.log_ohlc_status(f"Failed to change refresh rate to {rate_seconds}s")
                QMessageBox.warning(self, "Rate Change Failed", f"Failed to set refresh rate to {rate_seconds} seconds")

        except ValueError:
            self.log_ohlc_status("Invalid refresh rate format")
            QMessageBox.warning(self, "Invalid Rate", "Invalid refresh rate format")

    def log_ohlc_status(self, message):
        """Add message to OHLC status log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"{timestamp} - {message}"
        self.ohlc_status_log.append(log_message)

    def clear_ohlc_status_log(self):
        """Clear OHLC status log"""
        self.ohlc_status_log.clear()

    def update_ohlc_header_stats(self, status=None):
        """Update OHLC header statistics from engine"""
        try:
            # Get OHLC engine status if not provided
            if status is None:
                status = self.ohlc_engine.get_engine_status()

            # Update API status
            if status['running']:
                self.ohlc_api_status_label.setText("OHLC API Status: ●")
                self.ohlc_api_status_label.setStyleSheet("color: green;")
                self.ohlc_engine_status_label.setText("Engine: Running")
            else:
                self.ohlc_api_status_label.setText("OHLC API Status: ○")
                self.ohlc_api_status_label.setStyleSheet("color: gray;")
                self.ohlc_engine_status_label.setText("Engine: Stopped")

            # Update refresh rate
            self.ohlc_refresh_rate_label.setText(f"Refresh Rate: {status['refresh_interval']}s")

            # Update database records count (this would need OHLC database integration)
            self.ohlc_db_records_label.setText(f"OHLC Records: {status.get('loaded_assets_count', 0)} assets")

            # Update last fetch time
            last_fetch = status.get('last_fetch_time')
            if last_fetch:
                from datetime import datetime
                try:
                    last_fetch_dt = datetime.fromisoformat(last_fetch)
                    formatted_time = last_fetch_dt.strftime("%H:%M:%S")
                    self.ohlc_last_fetch_label.setText(f"Last Fetch: {formatted_time}")
                except:
                    self.ohlc_last_fetch_label.setText("Last Fetch: Recently")
            else:
                self.ohlc_last_fetch_label.setText("Last Fetch: Never")

            # Update activity panel statistics
            self.ohlc_fetch_count_label.setText(f"Fetches: {status['fetch_count']}")
            self.ohlc_success_rate_label.setText(f"Success: {status['success_rate']}%")

        except Exception as e:
            logger.error(f"Error updating OHLC header stats: {e}")

    def create_ohlc_archive(self):
        """Create OHLC archive with selected assets and date range"""
        try:
            # Check if archiver is ready
            if self.ohlc_archiver is None:
                QMessageBox.warning(self, "Initializing",
                                  "OHLC archive manager is still initializing. Please wait a moment and try again.")
                return

            # Get selected assets
            selected_items = self.ohlc_archive_asset_list.selectedItems()

            if not selected_items:
                QMessageBox.warning(self, "No Assets Selected", "Please select at least one asset to archive")
                return

            # Get selected asset IDs
            selected_asset_names = [item.text() for item in selected_items]
            available_assets = self.ohlc_archiver.get_available_assets()

            selected_asset_ids = []
            for asset in available_assets:
                if asset['display_name'] in selected_asset_names:
                    selected_asset_ids.append(asset['asset_id'])

            # Get selected date range
            start_date = self.ohlc_archive_start_date.date().toString("yyyy-MM-dd")
            end_date = self.ohlc_archive_end_date.date().toString("yyyy-MM-dd")

            # Validate date range
            if start_date > end_date:
                QMessageBox.warning(self, "Invalid Date Range", "Start date must be before or equal to end date")
                return

            # Confirmation dialog
            reply = QMessageBox.question(
                self, "Create OHLC Archive",
                f"Create OHLC archive with:\n\n"
                f"Assets: {len(selected_asset_ids)} selected\n"
                f"Date Range: {start_date} to {end_date}\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

            # Generate archive filename
            archive_filename = self.ohlc_archiver.generate_ohlc_archive_filename(start_date, end_date)

            # Get save location
            archive_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save OHLC Archive",
                archive_filename,
                "Database files (*.db);;All files (*.*)"
            )

            if not archive_path:
                return

            # Disable button during archive creation
            self.create_ohlc_archive_btn.setEnabled(False)
            self.create_ohlc_archive_btn.setText("Creating...")

            # Create progress dialog
            self.ohlc_archive_progress_dialog = QProgressDialog("Creating OHLC archive...", "Cancel", 0, 100, self)
            self.ohlc_archive_progress_dialog.setWindowModality(Qt.WindowModal)
            self.ohlc_archive_progress_dialog.setMinimumDuration(0)
            self.ohlc_archive_progress_dialog.canceled.connect(self.on_ohlc_archive_cancel)
            self.ohlc_archive_progress_dialog.show()

            # Start worker thread
            self.ohlc_archive_worker = OHLCArchiveWorker(
                self.ohlc_archiver,
                archive_path,
                selected_asset_ids,
                start_date,
                end_date
            )
            self.ohlc_archive_worker.progress_update.connect(self.on_ohlc_archive_progress)
            self.ohlc_archive_worker.archive_complete.connect(self.on_ohlc_archive_complete)
            self.ohlc_archive_worker.archive_error.connect(self.on_ohlc_archive_error)
            self.ohlc_archive_worker.start()

            self.log_ohlc_status(f"Starting OHLC archive creation: {os.path.basename(archive_path)}")

        except Exception as e:
            QMessageBox.critical(self, "Archive Error", f"Failed to start OHLC archive creation:\n\n{str(e)}")
            self.log_ohlc_status(f"OHLC archive creation failed: {str(e)}")
            logger.error(f"OHLC archive creation error: {e}")
            self.create_ohlc_archive_btn.setEnabled(True)
            self.create_ohlc_archive_btn.setText("Create Archive")

    def on_ohlc_archive_progress(self, message, percentage):
        """Handle OHLC archive progress updates from background thread"""
        if hasattr(self, 'ohlc_archive_progress_dialog') and self.ohlc_archive_progress_dialog:
            self.ohlc_archive_progress_dialog.setLabelText(message)
            self.ohlc_archive_progress_dialog.setValue(percentage)

    def on_ohlc_archive_cancel(self):
        """Handle OHLC archive cancellation"""
        if hasattr(self, 'ohlc_archive_worker') and self.ohlc_archive_worker:
            self.ohlc_archive_worker.cancel()
            self.log_ohlc_status("OHLC archive creation cancelled")

    def on_ohlc_archive_complete(self, archive_stats, archive_path):
        """Handle OHLC archive completion from background thread"""
        try:
            # Close progress dialog
            if hasattr(self, 'ohlc_archive_progress_dialog') and self.ohlc_archive_progress_dialog:
                self.ohlc_archive_progress_dialog.close()

            # Extract date range from worker
            start_date = self.ohlc_archive_worker.start_date
            end_date = self.ohlc_archive_worker.end_date

            # Show completion dialog
            completion_msg = (
                f"OHLC Archive Created Successfully!\n\n"
                f"Archive: {os.path.basename(archive_path)}\n"
                f"OHLC Records: {archive_stats.ohlc_data_records:,}\n"
                f"Metadata Records: {archive_stats.fetch_metadata_records:,}\n"
                f"Total Records: {archive_stats.total_records:,}\n"
                f"Archive Size: {archive_stats.archive_size_mb:.2f} MB\n\n"
                f"Date Range: {start_date} to {end_date}"
            )

            QMessageBox.information(self, "Archive Complete", completion_msg)
            self.log_ohlc_status(f"OHLC archive created: {os.path.basename(archive_path)}")

        finally:
            # Re-enable button
            self.create_ohlc_archive_btn.setEnabled(True)
            self.create_ohlc_archive_btn.setText("Create Archive")

    def on_ohlc_archive_error(self, error_msg):
        """Handle OHLC archive error from background thread"""
        try:
            # Close progress dialog
            if hasattr(self, 'ohlc_archive_progress_dialog') and self.ohlc_archive_progress_dialog:
                self.ohlc_archive_progress_dialog.close()

            QMessageBox.critical(self, "Archive Error", f"Failed to create OHLC archive:\n\n{error_msg}")
            self.log_ohlc_status(f"OHLC archive creation failed: {error_msg}")
            logger.error(f"OHLC archive creation error: {error_msg}")

        finally:
            # Re-enable button
            self.create_ohlc_archive_btn.setEnabled(True)
            self.create_ohlc_archive_btn.setText("Create Archive")

    def populate_ohlc_archive_assets(self):
        """Populate OHLC archive asset list from available assets in database"""
        try:
            self.ohlc_archive_asset_list.clear()

            # Check if archiver is ready
            if self.ohlc_archiver is None:
                logger.debug("OHLC archiver not ready yet, skipping populate")
                return

            # Get available assets from OHLC archiver
            available_assets = self.ohlc_archiver.get_available_assets()

            if available_assets:
                # Count assets by exchange segment
                nse_eq_count = 0
                nse_idx_count = 0
                other_count = 0

                for asset in available_assets:
                    display_name = asset['display_name']
                    self.ohlc_archive_asset_list.addItem(display_name)

                    # Count by segment type
                    segment_code = asset.get('segment_code', '')
                    if segment_code == 'NSE_EQ':
                        nse_eq_count += 1
                    elif segment_code == 'NSE_IDX':
                        nse_idx_count += 1
                    else:
                        other_count += 1

                # Update status label with count breakdown
                status_msg = f"{len(available_assets)} OHLC assets available ({nse_idx_count} indices, {nse_eq_count} stocks"
                if other_count > 0:
                    status_msg += f", {other_count} others"
                status_msg += ")"
                self.ohlc_archive_status_label.setText(status_msg)
            else:
                self.ohlc_archive_status_label.setText("No OHLC assets found in database")

            logger.info(f"Populated OHLC archive with {len(available_assets)} assets")

        except Exception as e:
            logger.error(f"Error populating OHLC archive assets: {e}")
            self.ohlc_archive_status_label.setText("Error loading available OHLC assets")
            self.log_ohlc_status(f"Error loading archive assets: {e}")

    def select_all_ohlc_archive_assets(self):
        """Select all OHLC assets for archival"""
        for i in range(self.ohlc_archive_asset_list.count()):
            item = self.ohlc_archive_asset_list.item(i)
            item.setSelected(True)

    def clear_ohlc_archive_assets(self):
        """Clear OHLC asset selection for archival"""
        self.ohlc_archive_asset_list.clearSelection()

    # ===== OHLC SCHEDULING METHODS =====

    def set_ohlc_frequency_combo(self):
        """Set OHLC frequency combo based on current schedule type"""
        current_type = self.ohlc_schedule_manager.schedule_config['schedule_type']
        if current_type == 'daily':
            self.ohlc_frequency_combo.setCurrentText("Daily")
        elif current_type == 'weekdays':
            self.ohlc_frequency_combo.setCurrentText("Weekdays")
        else:
            self.ohlc_frequency_combo.setCurrentText("Weekdays")  # Default

    def on_ohlc_schedule_enabled_changed(self, state):
        """Handle OHLC schedule enable/disable checkbox change"""
        enabled = state == 2  # Qt.Checked
        self.save_ohlc_schedule_settings()
        logger.info(f"OHLC schedule enabled: {enabled}")

    def on_ohlc_schedule_time_changed(self):
        """Handle OHLC start/end time changes"""
        self.save_ohlc_schedule_settings()

    def on_ohlc_schedule_type_changed(self):
        """Handle OHLC frequency combo changes"""
        self.save_ohlc_schedule_settings()

    def save_ohlc_schedule_settings(self):
        """Save current OHLC GUI schedule settings to OHLCScheduleManager"""
        try:
            enabled = self.ohlc_schedule_checkbox.isChecked()
            start_time = self.ohlc_start_time_combo.currentText()
            end_time = self.ohlc_end_time_combo.currentText()

            # Determine schedule type from frequency combo
            frequency_text = self.ohlc_frequency_combo.currentText()
            if frequency_text == "Daily":
                schedule_type = 'daily'
            elif frequency_text == "Weekdays":
                schedule_type = 'weekdays'
            else:
                schedule_type = 'weekdays'  # Default fallback

            # Update OHLC schedule manager
            self.ohlc_schedule_manager.update_schedule(
                enabled, start_time, end_time, schedule_type
            )

        except Exception as e:
            logger.error(f"Error saving OHLC schedule settings: {e}")

    def check_ohlc_schedule_trigger(self):
        """Check if OHLC scheduled collection should be triggered or stopped"""
        try:
            # Check if schedule is enabled
            if not self.ohlc_schedule_manager.schedule_config['enabled']:
                return

            # Get current OHLC engine status
            status = self.ohlc_engine.get_engine_status()
            is_running = status['running']

            # Check if we're within the collection window
            within_window = self.ohlc_schedule_manager.is_within_collection_window()

            if within_window and not is_running:
                # Should be running but isn't - start it
                if self.ohlc_loaded_assets:
                    logger.info("OHLC schedule trigger detected - starting OHLC engine")
                    self.log_ohlc_status("Schedule: Starting OHLC collection (within window)")
                    self.start_ohlc_collection()
                else:
                    logger.warning("OHLC schedule trigger detected but no assets loaded")

            elif not within_window and is_running:
                # Outside window but still running - stop it
                logger.info("OHLC schedule window ended - stopping OHLC engine")
                self.log_ohlc_status("Schedule: Stopping OHLC collection (window ended)")
                self.stop_ohlc_collection()

        except Exception as e:
            logger.error(f"Error in OHLC schedule trigger check: {e}")

    # ===== END OHLC SCHEDULING METHODS =====

    # ===== END OHLC ENGINE METHODS =====

    def signal_handler(self, signum, frame):
        """Handle system signals for clean shutdown"""
        logger.info(f"Received signal {signum}, initiating clean shutdown...")
        self.close()


class ContinuousCollectionWorker(QThread):
    """Background worker thread for continuous scheduled collection"""
    
    progress = pyqtSignal(str)  # current asset name
    asset_complete = pyqtSignal(str, bool)  # asset name, success
    loop_complete = pyqtSignal(int)  # loop number completed
    collection_finished = pyqtSignal(bool)  # stopped or completed
    schedule_status_changed = pyqtSignal(str)  # status message
    
    def __init__(self, engine, assets_to_collect: List, schedule_manager: ScheduleManager):
        super().__init__()
        self.engine = engine
        self.assets_to_collect = assets_to_collect
        self.schedule_manager = schedule_manager
        self.stop_requested = False
        self.pause_requested = False
        self.current_loop = 0
        self.total_collections = 0
        self.loop_start_time = None
    
    def run(self):
        """Execute continuous collection with schedule checking"""
        try:
            logger.info("Continuous collection worker started")
            self.schedule_status_changed.emit("Waiting for schedule window...")
            
            # Wait for schedule window to begin
            while not self.stop_requested:
                if self.schedule_manager.is_within_collection_window():
                    break
                
                # Check every 30 seconds when not in window
                for _ in range(30):
                    if self.stop_requested:
                        return
                    time.sleep(1)
            
            if self.stop_requested:
                return
            
            logger.info("Entered collection window - starting continuous loops")
            self.schedule_status_changed.emit("Collection window active - starting loops")
            
            # Main continuous collection loop
            while not self.stop_requested:
                # Check if still within schedule window
                if not self.schedule_manager.is_within_collection_window():
                    logger.info("Exited collection window - stopping loops")
                    self.schedule_status_changed.emit("Collection window ended")
                    break
                
                # Start new collection loop
                self.current_loop += 1
                self.loop_start_time = datetime.now()
                
                logger.info(f"Starting collection loop {self.current_loop}")
                self.schedule_status_changed.emit(f"Loop {self.current_loop} in progress")
                
                loop_success_count = 0
                loop_failed_count = 0
                
                # Collect all assets in this loop
                for asset in self.assets_to_collect:
                    # Check for stop or window exit before each asset
                    if self.stop_requested:
                        break
                    
                    if not self.schedule_manager.is_within_collection_window():
                        logger.info("Collection window ended mid-loop")
                        break
                    
                    # Handle pause requests (for manual collection conflicts)
                    while self.pause_requested and not self.stop_requested:
                        time.sleep(1)
                    
                    if self.stop_requested:
                        break
                    
                    # Emit progress
                    self.progress.emit(f"Loop {self.current_loop}: {asset.asset_name}")
                    
                    try:
                        # Perform collection with existing engine rate limiting
                        success = self.engine.collect_asset(
                            asset.asset_id, 
                            asset.segment_code, 
                            asset.number_expiries
                        )
                        
                        if success:
                            loop_success_count += 1
                            self.total_collections += 1
                        else:
                            loop_failed_count += 1
                        
                        # Emit asset completion
                        self.asset_complete.emit(asset.asset_name, success)
                        
                    except Exception as e:
                        logger.error(f"Error collecting {asset.asset_name} in loop {self.current_loop}: {e}")
                        loop_failed_count += 1
                        self.asset_complete.emit(asset.asset_name, False)
                
                # Loop completed (or interrupted)
                if not self.stop_requested:
                    loop_duration = (datetime.now() - self.loop_start_time).total_seconds()
                    logger.info(f"Loop {self.current_loop} completed: {loop_success_count} success, {loop_failed_count} failed, {loop_duration:.1f}s")
                    
                    self.loop_complete.emit(self.current_loop)
                    
                    # Brief pause before next loop (respects engine's rate limiting)
                    # No additional delay needed as engine handles rate limiting per asset
                    
                    # Check if still in window for next loop
                    if not self.schedule_manager.is_within_collection_window():
                        break
        
        except Exception as e:
            logger.error(f"Continuous collection worker error: {e}")
        
        finally:
            final_status = "stopped" if self.stop_requested else "completed"
            logger.info(f"Continuous collection {final_status} after {self.current_loop} loops, {self.total_collections} total collections")
            self.collection_finished.emit(self.stop_requested)
    
    def request_pause(self):
        """Pause collection (for manual collection conflicts)"""
        self.pause_requested = True
        logger.info("Continuous collection pause requested")
    
    def resume(self):
        """Resume collection after pause"""
        self.pause_requested = False
        logger.info("Continuous collection resumed")
    
    def get_stats(self) -> Dict:
        """Get current collection statistics"""
        return {
            'current_loop': self.current_loop,
            'total_collections': self.total_collections,
            'is_paused': self.pause_requested,
            'loop_start_time': self.loop_start_time.isoformat() if self.loop_start_time else None
        }


class CollectionWorker(QThread):
    """Background worker thread for asset collection"""
    
    progress = pyqtSignal(str)  # current asset name
    asset_complete = pyqtSignal(str, bool)  # asset name, success
    collection_finished = pyqtSignal(bool)  # stopped or completed
    
    def __init__(self, engine, assets_to_collect):
        super().__init__()
        self.engine = engine
        self.assets_to_collect = assets_to_collect
        self.stop_requested = False
    
    def run(self):
        """Execute collection in background thread"""
        try:
            for asset in self.assets_to_collect:
                if self.stop_requested:
                    break
                
                self.progress.emit(asset.asset_name)
                
                try:
                    success = self.engine.collect_asset(asset.asset_id, asset.segment_code, asset.number_expiries)
                    self.asset_complete.emit(asset.asset_name, success)
                except Exception as e:
                    logger.error(f"Error collecting {asset.asset_name}: {e}")
                    self.asset_complete.emit(asset.asset_name, False)
        
        except Exception as e:
            logger.error(f"Collection worker error: {e}")
        
        self.collection_finished.emit(self.stop_requested)


class ArchiveWorker(QThread):
    """Background worker thread for database archive creation"""
    
    progress_update = pyqtSignal(str, int)  # message, percentage
    archive_complete = pyqtSignal(ArchiveStats, str)  # stats, archive_path
    archive_error = pyqtSignal(str)  # error_message
    
    def __init__(self, archiver: DatabaseArchiver, archive_path: str, 
                 assets: Optional[List[str]], start_date: str, end_date: str):
        super().__init__()
        self.archiver = archiver
        self.archive_path = archive_path
        self.assets = assets
        self.start_date = start_date
        self.end_date = end_date
    
    def run(self):
        """Execute archive creation in background thread"""
        try:
            logger.info(f"Archive worker started: {self.archive_path}")
            
            # Define progress callback
            def progress_callback(message: str, percentage: int):
                self.progress_update.emit(message, percentage)
            
            # Create archive
            archive_stats = self.archiver.create_archive(
                archive_path=self.archive_path,
                assets=self.assets,
                start_date=self.start_date,
                end_date=self.end_date,
                progress_callback=progress_callback
            )
            
            # Emit completion
            self.archive_complete.emit(archive_stats, self.archive_path)
            logger.info(f"Archive worker completed successfully")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Archive worker error: {error_msg}")
            self.archive_error.emit(error_msg)


class OHLCArchiveWorker(QThread):
    """Background worker thread for OHLC archive creation"""

    progress_update = pyqtSignal(str, int)  # message, percentage
    archive_complete = pyqtSignal(object, str)  # OHLCArchiveStats, archive_path
    archive_error = pyqtSignal(str)  # error_message

    def __init__(self, ohlc_archiver, archive_path: str,
                 assets: List[str], start_date: str, end_date: str):
        super().__init__()
        self.ohlc_archiver = ohlc_archiver
        self.archive_path = archive_path
        self.assets = assets
        self.start_date = start_date
        self.end_date = end_date
        self.cancelled = False

    def run(self):
        """Execute OHLC archive creation in background thread"""
        try:
            logger.info(f"OHLC Archive worker started: {self.archive_path}")

            # Define progress callback
            def progress_callback(message: str, percentage: int):
                if self.cancelled:
                    raise Exception("Archive creation cancelled by user")
                self.progress_update.emit(message, percentage)

            # Create archive
            archive_stats = self.ohlc_archiver.create_ohlc_archive(
                archive_path=self.archive_path,
                start_date=self.start_date,
                end_date=self.end_date,
                assets=self.assets,
                progress_callback=progress_callback
            )

            # Emit completion
            self.archive_complete.emit(archive_stats, self.archive_path)
            logger.info(f"OHLC Archive worker completed successfully")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"OHLC Archive worker error: {error_msg}")
            self.archive_error.emit(error_msg)

    def cancel(self):
        """Cancel the archive operation"""
        self.cancelled = True


class StatsWorker(QThread):
    """Background worker thread for periodic statistics updates"""

    stats_ready = pyqtSignal(dict, dict, dict)  # engine_stats, ohlc_status, cache_status
    stats_error = pyqtSignal(str)

    def __init__(self, engine, ohlc_engine):
        super().__init__()
        self.engine = engine
        self.ohlc_engine = ohlc_engine

    def run(self):
        """Execute stats queries in background thread"""
        try:
            # Query all stats
            engine_stats = self.engine.get_stats()
            ohlc_status = self.ohlc_engine.get_engine_status()
            cache_status = self.engine.get_expiry_cache_status()

            # Emit results
            self.stats_ready.emit(engine_stats, ohlc_status, cache_status)

        except Exception as e:
            logger.error(f"StatsWorker error: {e}")
            self.stats_error.emit(str(e))


class CacheRefreshWorker(QThread):
    """Background worker thread for cache refresh/clear operations"""

    refresh_complete = pyqtSignal(dict)  # result dict
    refresh_error = pyqtSignal(str)

    def __init__(self, engine, operation='refresh'):
        super().__init__()
        self.engine = engine
        self.operation = operation  # 'refresh' or 'clear'

    def run(self):
        """Execute cache operation in background thread"""
        try:
            if self.operation == 'refresh':
                logger.info("CacheRefreshWorker: Starting cache refresh...")
                result = self.engine.refresh_expiry_cache()
            else:  # 'clear'
                logger.info("CacheRefreshWorker: Starting cache clear...")
                result = self.engine.clear_expiry_cache()

            self.refresh_complete.emit(result)
            logger.info(f"CacheRefreshWorker: {self.operation} operation completed")

        except Exception as e:
            logger.error(f"CacheRefreshWorker error: {e}")
            self.refresh_error.emit(str(e))


class InitializationWorker(QThread):
    """Background worker thread for initialization operations"""

    oi_assets_loaded = pyqtSignal(list, str)  # loaded_assets, csv_path
    ohlc_assets_loaded = pyqtSignal(list)  # ohlc_loaded_assets
    archive_assets_loaded = pyqtSignal(list)  # available_assets
    init_complete = pyqtSignal()
    init_error = pyqtSignal(str, str)  # operation_name, error_message

    def __init__(self, engine, ohlc_engine, archiver):
        super().__init__()
        self.engine = engine
        self.ohlc_engine = ohlc_engine
        self.archiver = archiver

    def run(self):
        """Execute initialization operations in background thread"""
        try:
            logger.info("InitializationWorker: Starting background initialization...")

            # 1. Load OI assets from CSV
            try:
                assets_csv_path = os.path.join(os.path.dirname(__file__), "assets", "assets_select.csv")
                if os.path.exists(assets_csv_path):
                    logger.info("InitializationWorker: Loading OI assets from CSV...")
                    loaded_assets = self.engine.load_assets(assets_csv_path)
                    if loaded_assets:
                        self.oi_assets_loaded.emit(loaded_assets, assets_csv_path)
                        logger.info(f"InitializationWorker: Loaded {len(loaded_assets)} OI assets")
                    else:
                        logger.warning("InitializationWorker: Failed to load OI assets")
                else:
                    logger.warning(f"InitializationWorker: Assets file not found: {assets_csv_path}")
            except Exception as e:
                logger.error(f"InitializationWorker: Error loading OI assets: {e}")
                self.init_error.emit("OI Assets Load", str(e))

            # 2. Load OHLC assets
            try:
                logger.info("InitializationWorker: Loading OHLC assets...")
                ohlc_loaded_assets = self.ohlc_engine.get_loaded_assets()
                if ohlc_loaded_assets:
                    self.ohlc_assets_loaded.emit(ohlc_loaded_assets)
                    logger.info(f"InitializationWorker: Loaded {len(ohlc_loaded_assets)} OHLC assets")
                else:
                    logger.warning("InitializationWorker: No OHLC assets loaded")
            except Exception as e:
                logger.error(f"InitializationWorker: Error loading OHLC assets: {e}")
                self.init_error.emit("OHLC Assets Load", str(e))

            # 3. Load archive assets
            try:
                logger.info("InitializationWorker: Loading archive assets...")
                available_assets = self.archiver.get_available_assets()
                self.archive_assets_loaded.emit(available_assets)
                logger.info(f"InitializationWorker: Loaded {len(available_assets)} archive assets")
            except Exception as e:
                logger.error(f"InitializationWorker: Error loading archive assets: {e}")
                self.init_error.emit("Archive Assets Load", str(e))

            # Emit completion
            self.init_complete.emit()
            logger.info("InitializationWorker: Background initialization completed")

        except Exception as e:
            logger.error(f"InitializationWorker: Critical error: {e}")
            self.init_error.emit("Initialization", str(e))


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    
    # Enable Ctrl+C handling
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    try:
        window = DhanControlGUI()
        
        # Setup signal handler for clean shutdown
        signal.signal(signal.SIGINT, window.signal_handler)
        signal.signal(signal.SIGTERM, window.signal_handler)
        
        # Enable periodic processing of events to handle signals
        timer = QTimer()
        timer.start(100)  # Process events every 100ms
        timer.timeout.connect(lambda: None)  # Do nothing, just process events

        window.show()
        sys.exit(app.exec_())
    except Exception as e:
        logger.error(f"Application error: {e}")
        QMessageBox.critical(None, "Application Error", f"Failed to start application: {e}")


class BatchProcessingWorker(QThread):
    """Worker thread for batch processing historical data"""
    progress = pyqtSignal(int, int, str)  # current, total, message
    finished_signal = pyqtSignal(bool, str)  # success, message

    def __init__(self, source_db, scores_db, underlying_symbol, expiry):
        super().__init__()
        self.source_db = source_db
        self.scores_db = scores_db
        self.underlying_symbol = underlying_symbol
        self.expiry = expiry

    def run(self):
        """Run batch processing"""
        try:
            from oi import ComponentCalculator

            self.progress.emit(0, 100, f"Initializing ComponentCalculator...")

            # Create calculator with selected databases (mode="batch" for shift_historical folder)
            calculator = ComponentCalculator(
                source_db=self.source_db,
                scores_db=self.scores_db,
                mode="batch"
            )

            self.progress.emit(10, 100, f"Starting batch processing for {self.underlying_symbol} {self.expiry}")

            # Run batch processing
            calculator.calculate_all_scores(self.underlying_symbol, self.expiry)

            self.progress.emit(100, 100, "Processing complete")
            self.finished_signal.emit(
                True,
                f"Successfully processed {self.underlying_symbol} {self.expiry}"
            )

        except Exception as e:
            logger.error(f"Batch processing error: {e}")
            import traceback
            traceback.print_exc()
            self.finished_signal.emit(False, f"Error: {str(e)}")


if __name__ == "__main__":
    main()