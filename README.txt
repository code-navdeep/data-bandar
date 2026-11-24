================================================================================
                             Data Bandar
                           by ChorBazaar
================================================================================

NEWER VERSION AVAILABLE
-----------------------
Data Bandar is the older version of this tool. Its successor is
data-collection, a separate repository on the same GitHub account as
this one:

   https://github.com/code-navdeep/data-collection


WHAT IS DATA BANDAR?
--------------------
Data Bandar is a comprehensive PyQt5 desktop application for managing and
controlling automated options data collection from Dhan API. Features include:
- Schedule-based data collection
- Real-time monitoring and control
- Database archiving and management
- OHLC data collection
- Asset configuration management


SYSTEM REQUIREMENTS
-------------------
- Python 3.9 or higher (Required)
  Download: https://www.python.org/downloads/

- Operating System:
  * Windows 10/11 (64-bit)
  * macOS 10.15 or later
  * Linux (with GUI support)

- Minimum 2GB RAM recommended
- Internet connection for Dhan API access
- Dhan API credentials (free from Dhan)


INSTALLATION INSTRUCTIONS
--------------------------

STEP 1: Install Python

   FOR WINDOWS:
   1. Download from: https://www.python.org/downloads/
   2. Click yellow "Download Python 3.11.x" button
   3. Run the installer (python-3.11.x-amd64.exe)
   4. ⚠️ IMPORTANT: Check "Add Python 3.11 to PATH" (bottom of installer)
   5. Click "Install Now"
   6. Wait for installation (2-3 minutes)
   7. Verify: Open Command Prompt, type: python --version

   FOR MAC:
   1. Download from: https://www.python.org/downloads/
   2. Click "Download Python 3.11.x" button
   3. Run the .pkg installer
   4. Follow installation wizard
   5. Enter Mac password when prompted
   6. Wait for installation
   7. Verify: Open Terminal, type: python3 --version

   FOR LINUX (Ubuntu/Debian):
   1. Open Terminal
   2. Type: sudo apt update
   3. Type: sudo apt install python3 python3-pip python3-venv python3-pyqt5
   4. Wait for installation
   5. Verify: Type: python3 --version

   Recommended: Python 3.11+
   Minimum Required: Python 3.9

STEP 2: Configure API Credentials
   1. Copy the file: .env.template
   2. Rename the copy to: .env
   3. Open the .env file in a text editor
   4. Replace the placeholders with your actual Dhan API credentials:
      * DHAN_CLIENT_ID=your_client_id
      * DHAN_ACCESS_TOKEN=your_access_token
   5. Save the file

   How to get Dhan API credentials:
   1. Login to Dhan Web Platform: https://web.dhan.co
   2. Go to Settings → API Management
   3. Create new API credentials
   4. Copy Client ID and Access Token

   Note: The .env file is private and should never be shared or committed
         to version control. It contains your API credentials.

STEP 3: Launch Application

   For Windows:
   - Double-click: databandar.bat

   For Mac/Linux:
   - Double-click: databandar.command
     (If it doesn't work, right-click > Open)

STEP 4: First Run (2-3 minutes)
   The launcher will automatically:
   1. Create a virtual environment (venv)
   2. Install all required dependencies
   3. Launch Data Bandar

   Subsequent runs are instant (no installation needed).


STOPPING THE APPLICATION
-------------------------

OPTION 1: Close the window (recommended)
   - Simply close the Data Bandar window using the X button
   - This is the cleanest way to stop

OPTION 2: Terminal interrupt
   - Press Ctrl+C in the terminal window where Data Bandar is running
   - This will gracefully stop the application


USING THE APPLICATION
----------------------

Data Bandar provides:

1. OPTIONS DATA COLLECTION
   - Start/Stop data collection
   - Monitor collection status
   - Select assets to track

2. SCHEDULE MANAGEMENT
   - Set collection schedules
   - Define trading hours, in 15-minute steps
   - Run every day, or on weekdays only
   - Automatic start/stop

3. DATABASE MANAGEMENT
   - View database statistics
   - Copy chosen assets' data into an archive file you name
   - Archives double as backups; the live database is left untouched
   - See each archive's size and record count when it is made

4. OHLC DATA COLLECTION
   - Collect live quotes (open, high, low, last price, volume) and
     market depth
   - Set the refresh rate
   - Monitor collection status
   - Archive OHLC data

5. ASSET CONFIGURATION
   - assets_select.csv loads automatically at start
   - Load a different asset list from a CSV file
   - Add or remove assets by editing the CSV (see CONFIGURING ASSETS)


DATA STORAGE
------------
- Databases stored in: data/ folder
- Data persists between runs
- Regular archiving recommended
- Use Archive Manager for backups


VIRTUAL ENVIRONMENT
-------------------
The launcher automatically creates and manages a virtual environment:
- Location: venv/ folder
- Contains all Python dependencies
- Isolated from system Python
- No conflicts with other Python projects
- Can be safely deleted to reset (will reinstall on next run)


TROUBLESHOOTING
----------------

Problem: "Python not found" error
Solution:
   1. Install Python 3.9+ from python.org
   2. Ensure "Add Python to PATH" was checked during installation
   3. Restart terminal/command prompt after installation
   4. Try: python3 instead of python (Mac/Linux)

Problem: Dependencies installation fails
Solution:
   1. Check internet connection
   2. Try deleting venv/ folder and restart
   3. Manually install: pip install -r requirements.txt
   4. Check Python version: python --version (must be 3.9+)

Problem: "Invalid credentials" or API errors
Solution:
   1. Verify .env file has correct credentials
   2. Check if access token expired
   3. Generate new credentials from Dhan web platform
   4. Ensure no extra spaces in .env file

Problem: Window doesn't open
Solution:
   1. Check terminal for error messages
   2. Verify PyQt5 installed: pip show PyQt5
   3. On Linux: Install GUI libraries (python3-pyqt5)
   4. Try running manually: python control.py

Problem: Collection not starting
Solution:
   1. Check Dhan API credentials are valid
   2. Verify internet connection
   3. Check assets are configured correctly
   4. Review error messages in the application log

Problem: Application not responding
Solution:
   1. Try Ctrl+C in the terminal window
   2. Use Task Manager (Windows) or Activity Monitor (Mac)
   3. Find "python" process running control.py
   4. Force quit/end task

Problem: "Port already in use" error
Solution:
   1. Check if another instance is running
   2. Close any running Data Bandar windows
   3. Use Task Manager/Activity Monitor to force quit if needed
   4. Restart your computer if issue persists


TECHNICAL DETAILS
-----------------
- Application: Python 3.9+ with PyQt5
- GUI Framework: PyQt5
- Data Source: Dhan API
- Configuration: .env file (environment variables)
- Dependencies: Listed in requirements.txt
- Virtual Environment: Automatically managed
- Data Storage: SQLite databases in data/ folder


UPDATING THE APPLICATION
-------------------------
1. Download the new version
2. Stop Data Bandar (close window or press Ctrl+C)
3. Replace old files with new files
4. Keep your .env file (credentials)
5. Keep your data/ folder (your databases)
6. Delete venv/ folder to force fresh dependency installation
7. Run databandar launcher - will reinstall dependencies if needed


DEPENDENCIES (automatically installed)
---------------------------------------
- PyQt5 (GUI framework)
- pandas (data manipulation)
- numpy (numerical operations)
- requests (API communication)
- python-dotenv (configuration management)
- pyzmq (inter-process communication)


KEY FEATURES
------------

• Desktop Application
  - Native PyQt5 GUI
  - Responsive interface
  - Real-time updates
  - No browser needed

• Automated Data Collection
  - Schedule-based collection
  - Configurable refresh rate for quotes
  - Multiple asset support
  - Automatic error recovery

• Schedule Management
  - Define trading hours
  - Every day, or weekdays only

• Database Management
  - SQLite storage
  - Archive chosen assets to a separate file
  - Archives double as backups
  - Statistics tracking

• Asset Configuration
  - CSV-based asset lists
  - One list loads automatically; others load from a file
  - Separate lists for option chains and for quotes
  - Ready-made lists for the Nifty and the Bank Nifty

• Virtual Environment
  - Automatic setup
  - Dependency isolation
  - Clean uninstall
  - No system pollution


CONFIGURING ASSETS
-------------------
Asset lists are CSV files in the assets/ folder:
- assets_select.csv - loaded automatically at start, for both option-chain
  collection and quotes: 3 indices and 81 stocks
- assets.csv - a wider list of 219 instruments; three indices in it set how
  many expiries to collect, the rest collect one
- nifty.csv - three indices (Nifty, Bank Nifty, Midcap Nifty) and 50 Nifty
  stocks
- banknifty.csv - the Bank Nifty index and its 12 banks

Load any of them, or a list of your own, with the file picker in the app.
Edit a CSV to add or remove assets.

Columns the app reads:
- asset_id        Dhan security ID of the instrument
- asset_name      the name shown in the app
- exchange        e.g. NSE
- segment_code    IDX_I for an index, NSE_EQ for a stock
- lot_size        contract lot size (option chains)
- number_expiries optional; how many expiries to collect (default 1)

Any other column is ignored.


PRIVACY & SECURITY
------------------
- .env.template provided as a configuration template
- Copy to .env and add your credentials (never share your .env file)
- .env file excluded from version control (.gitignore)
- API credentials stored locally, never transmitted except to Dhan API
- Data stored locally in data/ folder
- Virtual environment isolated from system


GETTING HELP
-------------
- Website: https://chorbazaar.co.in
- Check Python version compatibility
- Review error messages in the application log
- Check Dhan API status if connection fails


UNINSTALLING
------------
To completely remove Data Bandar:
1. Close the application
2. Delete the entire Data Bandar folder
3. That's it! No system changes were made.


LICENCE
-------
MIT - see LICENSE.

PyQt5, which draws the window, is licensed separately under the GPL. pip
installs it from requirements.txt; nothing of it is redistributed here.


================================================================================
                     From ChorBazaar - https://chorbazaar.co.in
================================================================================
