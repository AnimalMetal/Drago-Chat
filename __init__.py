# NVDA Chat Client - COMPLETE FINAL VERSION
# Save this entire file as: globalPlugins/nvdaChat/__init__.py
# Version: 1.0.1 - Fixed Enter key on chat list and removed Close button

import globalPluginHandler
from scriptHandler import script
import ui, tones, wx, gui, threading, os, json, sys, time, addonHandler, queue, nvwave
from datetime import datetime

addon_dir = os.path.dirname(__file__)
lib_path = os.path.join(addon_dir, "lib")
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

try:
    import requests
except ImportError:
    requests = None
try:
    import websocket
except ImportError:
    websocket = None

import addonHandler
addonHandler.initTranslation()

# Config path - save in NVDA's appdata folder (not in addon directory)
# This survives addon reinstalls and updates
NVDA_CONFIG_DIR = os.path.join(os.path.expandvars('%APPDATA%'), 'nvda')
CONFIG_PATH = os.path.join(NVDA_CONFIG_DIR, "NVDA Chat config.json")
DEFAULT_CONFIG = {
    "server_url": "http://tt.dragodark.com:8080", 
    "username": "", 
    "password": "", 
    "email": "", 
    "auto_connect": False, 
    "check_updates_on_startup": True,  # Check for updates when NVDA starts
    "show_timestamps": True,  # Show date/time in messages
    "sound_enabled": True, 
    "notifications_enabled": True, 
    "reconnect_attempts": 10, 
    "reconnect_delay": 5,
    # Local message saving
    "save_messages_locally": True,
    "messages_folder": os.path.join(os.path.expanduser("~"), "NVDA Chat Messages"),
    "muted_chats": [],  # List of chat IDs that are muted
    # Individual sound settings
    "sound_message_received": True,
    "sound_message_sent": True,
    "sound_user_online": True,
    "sound_user_offline": True,
    "sound_friend_request": True,
    "sound_error": True,
    "sound_connected": True,
    "sound_disconnected": True,
    "sound_group_message": True,  # New: Group message sound
    # Individual speak notification settings
    "speak_message_received": True,
    "speak_message_sent": False,
    "speak_user_online": True,
    "speak_user_offline": True,
    "speak_friend_request": True,
    "speak_group_message": True,  # New: Speak group messages
    # Read messages aloud when in chat window
    "read_messages_aloud": True
}

# Update check URL
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/AnimalMetal/nvda-chat/main/version.json"
TEST_UPDATE_MODE = False  # Set to True to test update system without GitHub

# Sound file paths - expects WAV files in the sounds folder
SOUNDS_DIR = os.path.join(addon_dir, "sounds")
SOUNDS = {
    "message_received": os.path.join(SOUNDS_DIR, "message_received.wav"),
    "message_sent": os.path.join(SOUNDS_DIR, "message_sent.wav"),
    "user_online": os.path.join(SOUNDS_DIR, "user_online.wav"),
    "user_offline": os.path.join(SOUNDS_DIR, "user_offline.wav"),
    "friend_request": os.path.join(SOUNDS_DIR, "friend_request.wav"),
    "error": os.path.join(SOUNDS_DIR, "error.wav"),
    "connected": os.path.join(SOUNDS_DIR, "connected.wav"),
    "disconnected": os.path.join(SOUNDS_DIR, "disconnected.wav"),
    "group_message": os.path.join(SOUNDS_DIR, "group_message.wav")  # New: Group message sound
}

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    
    __gestures__ = {
        "kb:NVDA+shift+c": "openChat",
        "kb:NVDA+shift+o": "connect",
        "kb:NVDA+shift+d": "disconnect"
    }
    
    def __init__(self):
        super().__init__()
        self.config = self.loadConfig()
        self.connected = False
        self.ws = None
        self.chat_window = None
        self.friends = []
        self.chats = {}
        self.unread_messages = {}
        self.token = None
        self.reconnect_count = 0
        self.message_queue = queue.Queue()
        self.manual_disconnect = False
        self.reconnect_timer = None
        if requests is None or websocket is None:
            wx.CallLater(1000, lambda: ui.message(_("Error: Libraries missing")))
            return
        self.createMenu()
        self.start_message_processor()
        
        # Auto-connect if enabled
        if self.config.get('auto_connect'):
            wx.CallLater(2000, self.connect)
        
        # Check for updates on startup if enabled
        if self.config.get('check_updates_on_startup', True):
            wx.CallLater(5000, lambda: self.check_for_updates(show_no_update=False))
    
    def createMenu(self):
        try:
            self.toolsMenu = gui.mainFrame.sysTrayIcon.toolsMenu
            self.chatMenuItem = self.toolsMenu.Append(wx.ID_ANY, "NVDA &Chat")
            gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda e: self.showChatWindow(), self.chatMenuItem)
        except Exception as e:
            import traceback
            ui.message(f"Menu creation error: {e}")
            traceback.print_exc()
    
    def loadConfig(self):
        """Load config and merge with defaults to preserve user settings on updates"""
        # Check for old config in addon directory and migrate it
        old_config_path = os.path.join(os.path.dirname(__file__), "config.json")
        if os.path.exists(old_config_path) and not os.path.exists(CONFIG_PATH):
            try:
                # Migrate old config to new location
                with open(old_config_path) as f:
                    old_config = json.load(f)
                
                # Ensure NVDA config directory exists
                os.makedirs(NVDA_CONFIG_DIR, exist_ok=True)
                
                # Save to new location
                with open(CONFIG_PATH, 'w') as f:
                    json.dump(old_config, f, indent=4)
                
                # Delete old config
                try:
                    os.remove(old_config_path)
                except:
                    pass  # If we can't delete, that's okay
                
                ui.message(_("Config migrated to NVDA folder"))
            except:
                pass  # If migration fails, just continue normally
        
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f:
                    saved_config = json.load(f)
                
                # Start with defaults
                merged_config = DEFAULT_CONFIG.copy()
                
                # Overlay saved settings (preserves user data)
                merged_config.update(saved_config)
                
                # Save merged config back (adds new default keys)
                with open(CONFIG_PATH, 'w') as f:
                    json.dump(merged_config, f, indent=4)
                
                return merged_config
        except: 
            pass
        return DEFAULT_CONFIG.copy()
    
    def saveConfig(self):
        try:
            # Ensure NVDA config directory exists
            os.makedirs(NVDA_CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, 'w') as f: json.dump(self.config, f, indent=4)
        except Exception as e: ui.message(f"Error: {e}")
    
    def playSound(self, s):
        # Check global sound_enabled and individual sound setting
        setting_key = f'sound_{s}'
        if self.config.get('sound_enabled') and self.config.get(setting_key, True) and s in SOUNDS:
            try:
                sound_file = SOUNDS[s]
                if os.path.exists(sound_file):
                    nvwave.playWaveFile(sound_file, asynchronous=True)
                else:
                    # Fallback to beep if sound file not found
                    tones.beep(800, 100)
            except: pass
    
    def start_message_processor(self):
        def process():
            try:
                while not self.message_queue.empty():
                    self.handle_message(self.message_queue.get_nowait())
            except: pass
            wx.CallLater(100, process)
        wx.CallLater(100, process)
    
    def handle_message(self, msg):
        t, d = msg.get('type'), msg.get('data', {})
        if t == 'new_message':
            cid, m = d.get('chat_id'), d.get('message')
            sender = m.get('sender', 'Unknown')
            message_text = m.get('message', '')
            
            # Check if this is a system message about admin transfer
            if sender == 'System' and 'transferred admin rights to' in message_text:
                # Reload chat data to get updated admin status
                self.load_chats()
                # If manage group dialog is open, refresh it
                if self.chat_window:
                    wx.CallAfter(self.chat_window.refresh_chats)
            
            # If chat doesn't exist locally, load all chats from server
            if cid not in self.chats:
                self.load_chats()
            
            # Save message locally if enabled (for both sent and received)
            if self.config.get('save_messages_locally', True):
                self.save_message_locally(cid, m)
            
            # Update last message timestamp for sorting
            if cid in self.chats:
                self.chats[cid]['last_message_time'] = m.get('timestamp', datetime.now().isoformat())
            
            # Don't play sound or count as unread if it's our own message
            if sender == self.config.get('username'):
                # This is our own message echoed back - just update the display
                if self.chat_window: self.chat_window.on_new_message(cid, m)
                return
            
            # Message from someone else - play sound and handle notification
            # Mark as unread if not viewing this chat
            viewing_this_chat = (self.chat_window and 
                               self.chat_window.IsShown() and 
                               self.chat_window.current_chat == cid and
                               self.chat_window.rightPanel.IsShown())
            
            if not viewing_this_chat:
                self.unread_messages[cid] = self.unread_messages.get(cid, 0) + 1
                if cid in self.chats:
                    self.chats[cid]['unread_count'] = self.unread_messages[cid]
            
            # Check if chat is muted
            is_muted = cid in self.config.get('muted_chats', [])
            
            # Play different sound for group vs private messages (only if not muted)
            if not is_muted:
                chat = self.chats.get(cid, {})
                is_group = chat.get('type') == 'group'
                if is_group:
                    self.playSound('group_message')
                else:
                    self.playSound('message_received')
            
            # Check if we're in the chat window and viewing this chat
            in_chat_window = viewing_this_chat
            
            # Check chat type for notifications
            chat = self.chats.get(cid, {})
            is_group = chat.get('type') == 'group'
            speak_setting = 'speak_group_message' if is_group else 'speak_message_received'
            
            # Speak notification based on settings and location (only if not muted)
            if not is_muted and self.config.get(speak_setting, True):
                # Only speak if NOT in chat window (ChatWindow.on_new_message will handle it)
                if not in_chat_window:
                    # Just say "Message from X" if outside chat window
                    if is_group:
                        group_name = chat.get('name', 'Group')
                        ui.message(f"{sender} in {group_name}")
                    else:
                        ui.message(_("Message from {user}").format(user=sender))
            
            if self.chat_window: self.chat_window.on_new_message(cid, m)
            
        elif t == 'user_online':
            u = d.get('username')
            self.playSound('user_online')
            if self.config.get('speak_user_online', True):
                ui.message(_("{user} is online").format(user=u))
            for f in self.friends:
                if f['username'] == u: f['status'] = 'online'; break
            if self.chat_window: wx.CallAfter(self.chat_window.refresh_friends)
            
        elif t == 'user_offline':
            u = d.get('username')
            self.playSound('user_offline')
            if self.config.get('speak_user_offline', True):
                ui.message(_("{user} is offline").format(user=u))
            for f in self.friends:
                if f['username'] == u: f['status'] = 'offline'; break
            if self.chat_window: wx.CallAfter(self.chat_window.refresh_friends)
            
        elif t == 'friend_request':
            self.playSound('friend_request')
            if self.config.get('speak_friend_request', True):
                ui.message(_("Friend request from {user}").format(user=d.get("from")))
            self.load_friends()
            
        elif t == 'friend_accepted':
            self.playSound('user_online')
            ui.message(_("{user} accepted friend request").format(user=d.get("username")))
            self.load_friends()
    
    @script(description="Open chat", category="NVDA Chat")
    def script_openChat(self, gesture): 
        try:
            self.showChatWindow()
        except Exception as e:
            import traceback
            ui.message(f"Script error: {e}")
            traceback.print_exc()
    
    @script(description="Connect", category="NVDA Chat")
    def script_connect(self, gesture):
        if not self.connected: self.manual_disconnect = False; wx.CallAfter(self.connect)
        else: ui.message(_("Connected"))
    
    @script(description="Disconnect", category="NVDA Chat")
    def script_disconnect(self, gesture):
        if self.connected: self.manual_disconnect = True; wx.CallAfter(self.disconnect)
        else: ui.message(_("Not connected"))
    
    def showChatWindow(self):
        try:
            if self.chat_window and self.chat_window.IsShown():
                # Window exists and is shown, just raise it
                self.chat_window.Raise()
            else:
                # Create new window
                self.chat_window = ChatWindow(gui.mainFrame, self)
                self.chat_window.Show()
                self.chat_window.Raise()
                ui.message(_("Chat window opened"))
        except Exception as e:
            import traceback
            ui.message(f"Error opening window: {e}")
            traceback.print_exc()
    
    def connect(self):
        if not self.config.get('username') or not self.config.get('password'):
            ui.message(_("Configure credentials"))
            wx.CallAfter(self.showChatWindow)
            return
        self.manual_disconnect = False
        threading.Thread(target=self._connect_thread, daemon=True).start()
    
    def _connect_thread(self):
        try:
            url = self.config['server_url']
            resp = requests.post(f'{url}/api/auth/login', json={'username': self.config['username'], 'password': self.config['password']}, timeout=10)
            if resp.status_code == 200:
                self.token = resp.json().get('token')
                self.connected = True
                was_reconnecting = self.reconnect_count > 0
                self.reconnect_count = 0
                
                # Only announce and beep if this was a manual connection (not auto-reconnect)
                if not was_reconnecting:
                    wx.CallAfter(lambda: (self.playSound('connected'), ui.message(_("Connected"))))
                # Silent reconnection - no beep, no message
                
                self.startWebSocket()
                wx.CallAfter(self.load_friends)
                wx.CallAfter(self.load_chats)
            else: wx.CallAfter(lambda: ui.message(_("Login failed")))
        except requests.exceptions.Timeout:
            if self.reconnect_count == 0:
                wx.CallAfter(lambda: ui.message(_("Timeout")))
            if not self.manual_disconnect: self.schedule_reconnect()
        except requests.exceptions.ConnectionError:
            if self.reconnect_count == 0:
                wx.CallAfter(lambda: ui.message(_("Server unreachable")))
            if not self.manual_disconnect: self.schedule_reconnect()
        except Exception as e: wx.CallAfter(lambda: ui.message(f"Error: {e}"))
    
    def schedule_reconnect(self):
        if self.reconnect_count >= self.config.get('reconnect_attempts', 5):
            # Don't announce here - it's announced in on_ws_close
            return
        self.reconnect_count += 1
        delay = self.config.get('reconnect_delay', 3)
        wx.CallLater(delay * 1000, self.connect)
    
    def startWebSocket(self):
        if not self.token: return
        def run_ws():
            try:
                ws_url = self.config['server_url'].replace('http://', 'ws://').replace('https://', 'wss://') + '/socket.io/?EIO=4&transport=websocket'
                self.ws = websocket.WebSocketApp(
                    ws_url, 
                    on_open=self.on_ws_open, 
                    on_message=self.on_ws_message, 
                    on_error=self.on_ws_error, 
                    on_close=self.on_ws_close,
                    on_ping=self.on_ws_ping,
                    on_pong=self.on_ws_pong
                )
                # Increase timeout and ping settings for more stability
                self.ws.run_forever(ping_interval=30, ping_timeout=20)
            except Exception as e:
                # If websocket fails to start, trigger reconnection silently
                if not self.manual_disconnect:
                    wx.CallAfter(self.schedule_reconnect)
        threading.Thread(target=run_ws, daemon=True).start()
    
    def on_ws_ping(self, ws, message):
        """Handle ping from server"""
        pass
    
    def on_ws_pong(self, ws, message):
        """Handle pong from server"""
        pass
    
    def on_ws_open(self, ws):
        try:
            # Reset reconnect count on successful connection
            self.reconnect_count = 0
            ws.send('40')
            time.sleep(0.1)
            auth_msg = f'42["authenticate",{{"token":"{self.token}"}}]'
            ws.send(auth_msg)
            # Start heartbeat to keep connection alive
            self.start_heartbeat()
        except: pass
    
    def start_heartbeat(self):
        """Send periodic heartbeat to keep connection alive"""
        def send_heartbeat():
            while self.connected and self.ws:
                try:
                    if self.ws:
                        # Send heartbeat every 15 seconds
                        heartbeat_msg = '42["heartbeat",{}]'
                        self.ws.send(heartbeat_msg)
                    time.sleep(15)
                except:
                    break
        threading.Thread(target=send_heartbeat, daemon=True).start()
    
    def on_ws_message(self, ws, msg):
        try:
            # Handle Socket.IO ping
            if msg == '2':
                # Server sent ping, respond with pong
                ws.send('3')
                return
            
            # Handle regular messages
            if msg.startswith('42'):
                data = json.loads(msg[2:])
                if isinstance(data, list) and len(data) >= 2:
                    event, payload = data[0], data[1]
                    self.message_queue.put({'type': event, 'data': payload})
        except: pass
    
    def on_ws_error(self, ws, error): pass
    
    def on_ws_close(self, ws, close_status_code, close_msg):
        was_connected = self.connected
        self.connected = False
        self.ws = None
        
        # If manually disconnected, disconnect() method already announced it
        if self.manual_disconnect:
            return
        
        # Connection lost - silently try to reconnect
        # No sounds, no messages during reconnection attempts
        if self.reconnect_count < self.config.get('reconnect_attempts', 3):
            wx.CallAfter(self.schedule_reconnect)
        else:
            # Only notify after all attempts exhausted
            wx.CallAfter(lambda: ui.message(_("Connection lost. Manual reconnect needed.")))
    
    def disconnect(self, silent=False):
        self.manual_disconnect = True
        self.connected = False
        if self.reconnect_timer:
            self.reconnect_timer.Stop()
            self.reconnect_timer = None
        if self.ws:
            try: self.ws.close()
            except: pass
            self.ws = None
        
        # Clear chat list when disconnected
        self.chats = {}
        if self.chat_window:
            wx.CallAfter(self.chat_window.refresh_chats)
        
        # Only play sound and announce if not silent
        if not silent:
            self.playSound('disconnected')
            ui.message(_("Disconnected"))
    
    def load_friends(self):
        if not self.token: return
        def load():
            try:
                resp = requests.get(f'{self.config["server_url"]}/api/friends', headers={'Authorization': f'Bearer {self.token}'}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    self.friends = data.get('friends', [])
                    if self.chat_window: wx.CallAfter(self.chat_window.refresh_friends)
            except: pass
        threading.Thread(target=load, daemon=True).start()
    
    def load_chats(self):
        if not self.token: return
        def load():
            try:
                resp = requests.get(f'{self.config["server_url"]}/api/chats', headers={'Authorization': f'Bearer {self.token}'}, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    chats = data.get('chats', [])
                    self.chats = {c['chat_id']: c for c in chats}
                    # Debug: Let user know how many chats loaded
                    if len(chats) > 0:
                        print(f"Loaded {len(chats)} chats: {list(self.chats.keys())}")
                    if self.chat_window: wx.CallAfter(self.chat_window.refresh_chats)
            except Exception as e:
                print(f"Error loading chats: {e}")
        threading.Thread(target=load, daemon=True).start()
    
    def delete_friend(self, username):
        if not self.token: return
        def delete():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/friends/delete', headers={'Authorization': f'Bearer {self.token}'}, json={'username': username}, timeout=10)
                if resp.status_code == 200: wx.CallAfter(lambda: (ui.message(_("Friend deleted")), self.load_friends()))
                else: wx.CallAfter(lambda: ui.message(_("Error deleting friend")))
            except: wx.CallAfter(lambda: ui.message(_("Connection error")))
        threading.Thread(target=delete, daemon=True).start()
    
    def delete_chat(self, chat_id):
        if not self.token: return
        def delete():
            try:
                resp = requests.delete(f'{self.config["server_url"]}/api/chats/delete/{chat_id}', headers={'Authorization': f'Bearer {self.token}'}, timeout=10)
                if resp.status_code == 200:
                    if chat_id in self.chats: del self.chats[chat_id]
                    wx.CallAfter(lambda: (ui.message(_("Chat deleted")), self.load_chats()))
                else: wx.CallAfter(lambda: ui.message(_("Error deleting chat")))
            except: wx.CallAfter(lambda: ui.message(_("Connection error")))
        threading.Thread(target=delete, daemon=True).start()
    
    
    # Group management methods
    def add_group_member(self, chat_id, username, callback=None):
        if not self.token: return
        def add():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/chats/group/add-member', headers={'Authorization': f'Bearer {self.token}'}, json={'chat_id': chat_id, 'username': username}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: ui.message(_("Added {user} to group").format(user=username)))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message(_("Error adding member")))
            except: wx.CallAfter(lambda: ui.message(_("Connection error")))
        threading.Thread(target=add, daemon=True).start()
    
    def remove_group_member(self, chat_id, username, callback=None):
        if not self.token: return
        def remove():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/chats/group/remove-member', headers={'Authorization': f'Bearer {self.token}'}, json={'chat_id': chat_id, 'username': username}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: ui.message(_("Removed {user} from group").format(user=username)))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message(_("Error removing member")))
            except: wx.CallAfter(lambda: ui.message(_("Connection error")))
        threading.Thread(target=remove, daemon=True).start()
    
    def rename_group(self, chat_id, new_name, callback=None):
        if not self.token: return
        def rename():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/chats/group/rename', headers={'Authorization': f'Bearer {self.token}'}, json={'chat_id': chat_id, 'new_name': new_name}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: ui.message(_("Group renamed to {name}").format(name=new_name)))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message(_("Error renaming group")))
            except: wx.CallAfter(lambda: ui.message(_("Connection error")))
        threading.Thread(target=rename, daemon=True).start()
    
    def delete_group(self, chat_id, callback=None):
        if not self.token: return
        def delete():
            try:
                resp = requests.delete(f'{self.config["server_url"]}/api/chats/group/delete/{chat_id}', headers={'Authorization': f'Bearer {self.token}'}, timeout=10)
                if resp.status_code == 200:
                    if chat_id in self.chats: del self.chats[chat_id]
                    wx.CallAfter(lambda: ui.message(_("Group deleted")))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message(_("Error deleting group")))
            except: wx.CallAfter(lambda: ui.message(_("Connection error")))
        threading.Thread(target=delete, daemon=True).start()
    
    def send_message(self, chat_id, message, is_action=False):
        if not self.ws or not self.connected: 
            # Silently fail if not connected - don't announce
            return
        try:
            # Include is_action flag in the message
            msg = f'42["send_message",{{"chat_id":"{chat_id}","message":"{message}","is_action":{str(is_action).lower()}}}]'
            self.ws.send(msg)
            self.playSound('message_sent')
            
            # Don't save here - server will echo it back and handle_message will save it
            # This prevents duplicate messages
            
            # Update last message timestamp
            if chat_id in self.chats:
                self.chats[chat_id]['last_message_time'] = datetime.now().isoformat()
            
        except Exception as e: 
            # Socket closed - silently ignore, reconnection will handle it
            pass
    
    def save_message_locally(self, chat_id, message):
        """Save message to local file as .txt"""
        try:
            if not self.config.get('save_messages_locally', True):
                return
            
            messages_folder = self.config.get('messages_folder', os.path.join(os.path.expanduser("~"), "NVDA Chat Messages"))
            
            # Create folder structure: messages_folder/username/chatname.txt
            user_folder = os.path.join(messages_folder, self.config.get('username', 'unknown'))
            os.makedirs(user_folder, exist_ok=True)
            
            # Get chat name for filename
            chat_name = self.get_chat_name(chat_id)
            chat_file = os.path.join(user_folder, f"{chat_name}.txt")
            
            # Format message for .txt file
            sender = message.get('sender', 'Unknown')
            text = message.get('message', '')
            is_action = message.get('is_action', False)
            
            # Always use current PC time for timestamp
            now = datetime.now()
            print(f"DEBUG: datetime.now() = {now}")
            print(f"DEBUG: datetime.now() formatted = {now.strftime('%Y-%m-%d %H:%M:%S')}")
            date_str = now.strftime('%Y-%m-%d %H:%M:%S')
            
            # Format message line
            if is_action:
                message_line = f"{sender} {text} ; {date_str}\n"
            else:
                message_line = f"{sender}; {text} ; {date_str}\n"
            
            # Append message to file
            with open(chat_file, 'a', encoding='utf-8') as f:
                f.write(message_line)
        except Exception as e:
            # Silently fail if can't save
            print(f"Error saving message: {e}")
    
    def get_chat_name(self, chat_id):
        """Get chat name for filename"""
        if chat_id in self.chats:
            chat = self.chats[chat_id]
            name = chat.get('name', '')
            if not name and chat.get('type') == 'private':
                others = [p for p in chat['participants'] if p != self.config.get('username')]
                name = others[0] if others else 'Unknown'
            return name if name else chat_id
        return chat_id
    
    def load_messages_locally(self, chat_id):
        """Load messages from local .txt file"""
        try:
            if not self.config.get('save_messages_locally', True):
                return []
            
            messages_folder = self.config.get('messages_folder', os.path.join(os.path.expanduser("~"), "NVDA Chat Messages"))
            user_folder = os.path.join(messages_folder, self.config.get('username', 'unknown'))
            
            # Get chat name for filename
            chat_name = self.get_chat_name(chat_id)
            chat_file = os.path.join(user_folder, f"{chat_name}.txt")
            
            if os.path.exists(chat_file):
                messages = []
                with open(chat_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        # Parse the line back to message format
                        # Format: "username; message ; timestamp" or "username action ; timestamp"
                        try:
                            # Check if it's an action (no semicolon after first word)
                            parts = line.split(' ; ')
                            if len(parts) >= 2:
                                timestamp = parts[-1]
                                content = ' ; '.join(parts[:-1])
                                
                                # Check if action format (username message) or regular (username; message)
                                if '; ' in content:
                                    sender, message_text = content.split('; ', 1)
                                    is_action = False
                                else:
                                    parts2 = content.split(' ', 1)
                                    sender = parts2[0]
                                    message_text = parts2[1] if len(parts2) > 1 else ''
                                    is_action = True
                                
                                messages.append({
                                    'sender': sender,
                                    'message': message_text,
                                    'timestamp': timestamp,
                                    'is_action': is_action
                                })
                        except:
                            continue
                
                return messages
        except:
            pass
        return []
    
    def create_chat(self, participants, callback=None, chat_type='private', group_name=''):
        if not self.token: return
        def create():
            try:
                payload = {'participants': participants, 'type': chat_type}
                if chat_type == 'group':
                    payload['name'] = group_name
                
                resp = requests.post(f'{self.config["server_url"]}/api/chats/create', headers={'Authorization': f'Bearer {self.token}'}, json=payload, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    chat_id = data.get('chat_id')
                    
                    if chat_id and chat_id not in self.chats:
                        self.chats[chat_id] = {
                            'chat_id': chat_id,
                            'type': chat_type,
                            'participants': participants,
                            'name': group_name if chat_type == 'group' else '',
                            'admin': self.config.get('username') if chat_type == 'group' else None,
                            'unread_count': 0
                        }
                    
                    self.load_chats()
                    
                    if callback: wx.CallAfter(callback, chat_id)
            except Exception as e:
                print(f"Error creating chat: {e}")
                wx.CallAfter(lambda: ui.message(_("Error creating chat")))
        threading.Thread(target=create, daemon=True).start()
    

    
    def transfer_admin(self, chat_id, new_admin, callback=None):
        """Transfer admin rights to another member"""
        if not self.token: return
        def transfer():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/chats/group/transfer-admin', headers={'Authorization': f'Bearer {self.token}'}, json={'chat_id': chat_id, 'new_admin': new_admin}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: ui.message(f"Transferred admin to {new_admin}"))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message(_("Error transferring admin")))
            except: wx.CallAfter(lambda: ui.message(_("Connection error")))
        threading.Thread(target=transfer, daemon=True).start()

    def check_for_updates(self, show_no_update=True):
        """Check for addon updates from GitHub"""
        def check():
            try:
                from logHandler import log
                
                # Get current version from manifest
                import configobj
                # manifest.ini is in addon root, not in globalPlugins/nvdaChat
                manifest_path = os.path.join(addon_dir, "..", "..", "manifest.ini")
                manifest_path = os.path.normpath(manifest_path)  # Clean up the path
                log.info(f"NVDA Chat: Reading manifest from {manifest_path}")
                
                manifest = configobj.ConfigObj(manifest_path, encoding='utf-8')
                
                # Try different ways to get version
                if 'version' in manifest:
                    current_version = manifest['version']
                elif hasattr(manifest, 'version'):
                    current_version = manifest.version
                else:
                    # Fallback: read file directly
                    log.warning("NVDA Chat: Could not read version from configobj, trying direct read")
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.startswith('version'):
                                current_version = line.split('=')[1].strip()
                                break
                        else:
                            raise Exception("Could not find version in manifest.ini")
                
                log.info(f"NVDA Chat: Current version: {current_version}")
                
                # Fetch latest version info
                response = requests.get(UPDATE_CHECK_URL, timeout=10)
                if response.status_code != 200:
                    wx.CallAfter(lambda: ui.message(_("Could not check for updates")))
                    return
                
                data = response.json()
                latest_version = data.get('version', '0.0.0')
                download_url = data.get('downloadURL', '')  # YOUR format uses downloadURL
                changelog = data.get('changelog', '')
                
                log.info(f"NVDA Chat: Latest version: {latest_version}")
                
                # Compare versions
                def version_tuple(v):
                    return tuple(map(int, v.split('.')))
                
                if version_tuple(latest_version) > version_tuple(current_version):
                    # New version available
                    message = _("New version available: {version}\n\n{changelog}\n\nDownload now?").format(
                        version=latest_version,
                        changelog=changelog
                    )
                    wx.CallAfter(lambda: self.show_update_dialog(message, download_url, latest_version))
                elif show_no_update:
                    wx.CallAfter(lambda: ui.message(_("You have the latest version ({version})").format(version=current_version)))
            except Exception as e:
                # Capture error message immediately to avoid closure issues
                error_msg = str(e)
                from logHandler import log
                log.error(f"NVDA Chat: Update check error: {error_msg}")
                import traceback
                log.error(traceback.format_exc())
                wx.CallAfter(lambda msg=error_msg: ui.message(_("Error checking for updates: {error}").format(error=msg)))
        
        threading.Thread(target=check, daemon=True).start()
    
    def show_update_dialog(self, message, download_url, version):
        """Show update available dialog"""
        dlg = wx.MessageDialog(
            None,
            message,
            _("Update Available"),
            wx.YES_NO | wx.ICON_INFORMATION
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            # Download and install
            self.download_update(download_url, version)
        dlg.Destroy()
    
    def download_update(self, download_url, version):
        """Download and install update - NEVER use browser"""
        def download():
            try:
                from logHandler import log
                log.info(f"NVDA Chat UPDATE: Starting download from {download_url}")
                log.info(f"NVDA Chat UPDATE: Python version: {sys.version}")
                
                ui.message(_("Downloading update..."))
                
                # Ensure NO browser opens - set environment
                import os
                os.environ['BROWSER'] = ''  # Suppress any browser opening
                
                # Download with requests (pure Python, no browser)
                log.info("NVDA Chat UPDATE: Calling requests.get()")
                response = requests.get(download_url, timeout=30, allow_redirects=True, stream=False)
                
                log.info(f"NVDA Chat UPDATE: Response status: {response.status_code}")
                log.info(f"NVDA Chat UPDATE: Content-Type: {response.headers.get('Content-Type', 'unknown')}")
                log.info(f"NVDA Chat UPDATE: Content-Length: {len(response.content)} bytes")
                
                if response.status_code == 200:
                    # Ensure Downloads folder exists
                    downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
                    if not os.path.exists(downloads_folder):
                        log.info(f"NVDA Chat UPDATE: Creating {downloads_folder}")
                        os.makedirs(downloads_folder)
                    
                    filename = f"nvda-chat-{version}.nvda-addon"
                    filepath = os.path.join(downloads_folder, filename)
                    
                    log.info(f"NVDA Chat UPDATE: Writing to {filepath}")
                    
                    # Write file
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                    
                    # Verify
                    file_size = os.path.getsize(filepath)
                    log.info(f"NVDA Chat UPDATE: File written successfully: {file_size} bytes")
                    log.info(f"NVDA Chat UPDATE: File exists check: {os.path.exists(filepath)}")
                    
                    if not os.path.exists(filepath) or file_size == 0:
                        log.error("NVDA Chat UPDATE: File verification failed!")
                        wx.CallAfter(lambda: ui.message(_("Download failed: File not saved")))
                        return
                    
                    ui.message(_("Download complete. Installing..."))
                    log.info("NVDA Chat UPDATE: Calling install_update()")
                    
                    # Wait to ensure file system sync
                    import time
                    time.sleep(1)
                    
                    # Install WITHOUT opening browser
                    wx.CallAfter(lambda: self.install_update(filepath, version))
                else:
                    log.error(f"NVDA Chat UPDATE: HTTP error {response.status_code}")
                    wx.CallAfter(lambda: ui.message(_("Download failed: Status {status}").format(status=response.status_code)))
            except Exception as e:
                from logHandler import log
                import traceback
                log.error(f"NVDA Chat UPDATE: Download exception: {e}")
                log.error(traceback.format_exc())
                wx.CallAfter(lambda: ui.message(_("Download error: {error}").format(error=str(e))))
        
        # Run in thread
        threading.Thread(target=download, daemon=True, name="NVDAChatUpdateDownload").start()
    
    def install_update(self, filepath, version):
        """Install the downloaded update by executing the addon file"""
        try:
            from logHandler import log
            log.info(f"NVDA Chat UPDATE: install_update called")
            log.info(f"NVDA Chat UPDATE: File: {filepath}")
            log.info(f"NVDA Chat UPDATE: File exists: {os.path.exists(filepath)}")
            
            if not os.path.exists(filepath):
                ui.message(_("Installation error: File not found"))
                return
            
            file_size = os.path.getsize(filepath)
            log.info(f"NVDA Chat UPDATE: File size: {file_size}")
            
            # Show confirmation dialog
            message = _(
                "Update downloaded successfully!\n\n"
                "Version: {version}\n"
                "Size: {size} KB\n\n"
                "The installer will open now.\n"
                "Follow NVDA's prompts to install.\n\n"
                "Continue?"
            ).format(version=version, size=file_size//1024)
            
            import wx
            dlg = wx.MessageDialog(
                None,
                message,
                _("Install Update"),
                wx.YES_NO | wx.ICON_QUESTION
            )
            
            result = dlg.ShowModal()
            dlg.Destroy()
            
            if result == wx.ID_YES:
                log.info("NVDA Chat UPDATE: User confirmed, launching installer")
                
                # Method 1: Use Windows shell to execute the file
                # This will open it with NVDA (the default handler for .nvda-addon files)
                import subprocess
                import sys
                
                try:
                    # Get NVDA executable path
                    nvda_exe = sys.executable
                    log.info(f"NVDA Chat UPDATE: NVDA executable: {nvda_exe}")
                    
                    # Launch NVDA with the addon file
                    # This is equivalent to: nvda.exe "path\to\addon.nvda-addon"
                    subprocess.Popen([nvda_exe, filepath], shell=False)
                    
                    log.info("NVDA Chat UPDATE: Installer launched successfully")
                    ui.message(_("Installer opened. Follow the prompts to install and restart NVDA."))
                    
                except Exception as e:
                    log.error(f"NVDA Chat UPDATE: Subprocess failed: {e}")
                    
                    # Fallback: Use Windows start command
                    try:
                        log.info("NVDA Chat UPDATE: Trying Windows start command")
                        subprocess.Popen(['cmd', '/c', 'start', '', filepath], shell=False)
                        ui.message(_("Installer opened. Follow the prompts."))
                    except Exception as e2:
                        log.error(f"NVDA Chat UPDATE: Start command failed: {e2}")
                        ui.message(_("Could not open installer automatically. Please open it manually from: {path}").format(path=filepath))
            else:
                log.info("NVDA Chat UPDATE: User cancelled")
                ui.message(_("Update saved in Downloads folder: {path}").format(path=filepath))
            
        except Exception as e:
            from logHandler import log
            import traceback
            log.error(f"NVDA Chat UPDATE: Error: {e}")
            log.error(traceback.format_exc())
            ui.message(_("Update downloaded to: {path}").format(path=filepath))
    def terminate(self):
        self.disconnect(silent=True)  # Silent disconnect on NVDA restart
        try:
            if self.chatMenuItem: self.toolsMenu.Remove(self.chatMenuItem)
        except: pass
        super().terminate()
    

class ChatWindow(wx.Frame):
    def __init__(self, parent, plugin):
        super().__init__(parent, title=_("NVDA Chat"), size=(800, 600))
        self.plugin = plugin
        self.current_chat = None
        self.message_history = []  # All messages for current chat
        self.history_position = -1  # Current position in history (-1 = at end/newest)
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.Bind(wx.EVT_CHAR_HOOK, self.onKeyPress)
        
        panel = wx.Panel(self)
        mainSizer = wx.BoxSizer(wx.HORIZONTAL)
        leftPanel = wx.Panel(panel)
        leftSizer = wx.BoxSizer(wx.VERTICAL)
        leftSizer.Add(wx.StaticText(leftPanel, label=_("Chats")), flag=wx.ALL, border=5)
        self.chatsList = wx.ListBox(leftPanel, style=wx.LB_SINGLE)
        self.chatsList.Bind(wx.EVT_CHAR_HOOK, self.onChatsListChar)
        self.chatsList.Bind(wx.EVT_RIGHT_DOWN, self.onChatsListRightClick)
        self.chatsList.Bind(wx.EVT_CONTEXT_MENU, self.onChatsListContextMenu)
        leftSizer.Add(self.chatsList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        leftBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in [(_("&New Chat"), self.onNewChat), (_("&Delete"), self.onDeleteChat), (_("&Account"), self.onAccount)]:
            btn = wx.Button(leftPanel, label=label)
            btn.Bind(wx.EVT_BUTTON, handler)
            leftBtnSizer.Add(btn, flag=wx.ALL, border=5)
        leftSizer.Add(leftBtnSizer)
        leftPanel.SetSizer(leftSizer)
        mainSizer.Add(leftPanel, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Right panel
        self.rightPanel = wx.Panel(panel)
        rightSizer = wx.BoxSizer(wx.VERTICAL)
        topSizer = wx.BoxSizer(wx.HORIZONTAL)
        backBtn = wx.Button(self.rightPanel, label=_("&Back"))
        backBtn.Bind(wx.EVT_BUTTON, self.onBack)
        topSizer.Add(backBtn, flag=wx.ALL, border=5)
        self.chatTitle = wx.StaticText(self.rightPanel, label="")
        topSizer.Add(self.chatTitle, flag=wx.ALL|wx.ALIGN_CENTER_VERTICAL, border=5)
        rightSizer.Add(topSizer, flag=wx.EXPAND)
        rightSizer.Add(wx.StaticText(self.rightPanel, label=_("Chat History")), flag=wx.ALL, border=5)
        self.messagesText = wx.TextCtrl(self.rightPanel, style=wx.TE_MULTILINE|wx.TE_READONLY|wx.TE_RICH2|wx.TE_DONTWRAP)
        
        # Bind Page Up/Down to chat history - use CHAR_HOOK to catch before cursor moves
        self.messagesText.Bind(wx.EVT_CHAR_HOOK, self.onHistoryCharHook)
        
        rightSizer.Add(self.messagesText, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        rightSizer.Add(wx.StaticText(self.rightPanel, label=_("Chat")), flag=wx.ALL, border=5)
        inputSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.messageInput = wx.TextCtrl(self.rightPanel, style=wx.TE_PROCESS_ENTER)
        self.messageInput.Bind(wx.EVT_TEXT_ENTER, self.onSendMessage)
        
        # Bind Page Up/Down for message history navigation - use CHAR_HOOK
        self.messageInput.Bind(wx.EVT_CHAR_HOOK, self.onInputCharHook)
        
        inputSizer.Add(self.messageInput, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        sendBtn = wx.Button(self.rightPanel, label=_("&Send"))
        sendBtn.Bind(wx.EVT_BUTTON, self.onSendMessage)
        inputSizer.Add(sendBtn, flag=wx.ALL, border=5)
        rightSizer.Add(inputSizer, flag=wx.EXPAND)
        self.rightPanel.SetSizer(rightSizer)
        mainSizer.Add(self.rightPanel, proportion=2, flag=wx.ALL|wx.EXPAND, border=5)
        self.rightPanel.Hide()
        
        panel.SetSizer(mainSizer)
        menuBar = wx.MenuBar()
        fileMenu = wx.Menu()
        connectItem = fileMenu.Append(wx.ID_ANY, _("&Connect\tCtrl+C"))
        self.Bind(wx.EVT_MENU, lambda e: self.onConnect(), connectItem)
        disconnectItem = fileMenu.Append(wx.ID_ANY, _("&Disconnect\tCtrl+D"))
        self.Bind(wx.EVT_MENU, lambda e: self.onDisconnect(), disconnectItem)
        fileMenu.AppendSeparator()
        exitItem = fileMenu.Append(wx.ID_EXIT, _("E&xit\tAlt+F4"))
        self.Bind(wx.EVT_MENU, self.onClose, exitItem)
        menuBar.Append(fileMenu, _("&File"))
        friendsMenu = wx.Menu()
        manageFriendsItem = friendsMenu.Append(wx.ID_ANY, _("&Manage Friends\tCtrl+F"))
        self.Bind(wx.EVT_MENU, self.onManageFriends, manageFriendsItem)
        menuBar.Append(friendsMenu, _("F&riends"))
        settingsMenu = wx.Menu()
        settingsItem = settingsMenu.Append(wx.ID_ANY, _("&Settings\tCtrl+S"))
        self.Bind(wx.EVT_MENU, self.onSettings, settingsItem)
        menuBar.Append(settingsMenu, _("&Settings"))
        self.SetMenuBar(menuBar)
        self.refresh_chats()
        self.Maximize()
    
    def format_timestamp(self, timestamp_str):
        """Format timestamp - already in local time from file"""
        try:
            # Timestamps in file are already in local time (YYYY-MM-DD HH:MM:SS)
            # Just return as-is if it's in the right format
            if len(timestamp_str) == 19 and timestamp_str[10] == ' ':
                return timestamp_str
            # Otherwise try to parse and format
            dt = datetime.fromisoformat(timestamp_str)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            return timestamp_str if timestamp_str else 'Unknown date'
    
    def onKeyPress(self, e):
        key = e.GetKeyCode()
        if key == wx.WXK_ESCAPE: self.Close()
        else: e.Skip()
    
    def onChatsListChar(self, e):
        """Handle key presses in the chats list using CHAR_HOOK"""
        key = e.GetKeyCode()
        if key == wx.WXK_RETURN or key == wx.WXK_NUMPAD_ENTER:
            # Enter key pressed - open the selected chat
            sel = self.chatsList.GetSelection()
            if sel != wx.NOT_FOUND and self.plugin.chats:
                self.onChatSelect(None)
                return
        elif key == ord('M'):
            # M key - Manage group (if admin)
            sel = self.chatsList.GetSelection()
            if sel != wx.NOT_FOUND and self.plugin.chats:
                sorted_chats = sorted(self.plugin.chats.items(), key=lambda x: x[1].get('last_message_time', ''), reverse=True)
                if sel < len(sorted_chats):
                    chat_id, chat = sorted_chats[sel]
                    if chat.get('type') == 'group':
                        if chat.get('admin') == self.plugin.config.get('username'):
                            self.on_manage_group(chat_id)
                            return
                        else:
                            ui.message(_("Only group admin can manage group"))
                            return
                    else:
                        ui.message(_("Not a group chat"))
                        return
        elif key == ord('V'):
            # V key - View members
            sel = self.chatsList.GetSelection()
            if sel != wx.NOT_FOUND and self.plugin.chats:
                sorted_chats = sorted(self.plugin.chats.items(), key=lambda x: x[1].get('last_message_time', ''), reverse=True)
                if sel < len(sorted_chats):
                    chat_id, chat = sorted_chats[sel]
                    if chat.get('type') == 'group':
                        self.on_view_members(chat_id)
                        return
                    else:
                        ui.message(_("Not a group chat"))
                        return
        e.Skip()
    
    def refresh_chats(self):
        self.chatsList.Clear()
        
        # Debug output
        print(f"Refreshing chats. Total chats: {len(self.plugin.chats)}")
        
        if not self.plugin.chats:
            self.chatsList.Append(_("No chats"))
        else:
            # Sort chats by most recent message (newest first)
            sorted_chats = sorted(
                self.plugin.chats.items(),
                key=lambda x: x[1].get('last_message_time', ''),
                reverse=True
            )
            
            for cid, c in sorted_chats:
                name = c.get('name', '')
                chat_type = c.get('type', 'private')
                
                # Get name for private chats
                if not name and chat_type == 'private':
                    others = [p for p in c['participants'] if p != self.plugin.config['username']]
                    name = others[0] if others else "Unknown"
                
                # Add (Group) indicator for groups
                if chat_type == 'group':
                    admin = c.get('admin', '')
                    current_user = self.plugin.config.get('username', '')
                    print(f"Checking admin for {name}: admin={admin}, current_user={current_user}, match={admin == current_user}")
                    is_admin = admin == current_user
                    if is_admin:
                        name = f"{name} ({_('Group - You are admin')})"
                    else:
                        name = f"{name} ({_('Group')})"
                
                # For private chats, add online/offline status
                if chat_type == 'private' and name != "Unknown":
                    # Check if the other person is online
                    is_online = False
                    for friend in self.plugin.friends:
                        if friend['username'] == name:
                            is_online = (friend.get('status', 'offline') == 'online')
                            break
                    
                    # Add text status indicator (screen reader friendly)
                    status_text = _("online") if is_online else _("offline")
                    name = f"{name} ({status_text})"
                
                # Check if chat is muted
                is_muted = cid in self.plugin.config.get('muted_chats', [])
                
                # Add unread count if any
                unread = c.get('unread_count', 0)
                if unread > 0:
                    display = f"{name} - {unread} {_('unread')}"
                else:
                    display = name
                
                # Add muted indicator
                if is_muted:
                    display = f"{display} [{_('Muted')}]"
                
                self.chatsList.Append(display)
                print(f"  Added to list: {display}")
    
    def refresh_friends(self): 
        # Refresh chat list when friend status changes to update online indicators
        self.refresh_chats()
    
    def onChatSelect(self, e):
        sel = self.chatsList.GetSelection()
        if sel == wx.NOT_FOUND or not self.plugin.chats: return
        
        # Get sorted chat list to match display order
        sorted_chats = sorted(
            self.plugin.chats.items(),
            key=lambda x: x[1].get('last_message_time', ''),
            reverse=True
        )
        
        if sel >= len(sorted_chats): return
        chat_id, chat = sorted_chats[sel]
        
        self.current_chat = chat_id
        name = chat.get('name', '')
        if not name and chat['type'] == 'private':
            others = [p for p in chat['participants'] if p != self.plugin.config['username']]
            name = others[0] if others else "Unknown"
        self.chatTitle.SetLabel(name)
        
        # Mark chat as read
        if chat_id in self.plugin.unread_messages:
            self.plugin.unread_messages[chat_id] = 0
        if chat_id in self.plugin.chats:
            self.plugin.chats[chat_id]['unread_count'] = 0
        
        # Refresh to update unread count display
        self.refresh_chats()
        self.chatsList.SetSelection(sel)  # Restore selection after refresh
        
        # Show the right panel when a chat is selected
        if not self.rightPanel.IsShown():
            self.rightPanel.Show()
            self.Layout()
        
        self.load_messages(chat_id)
        self.messageInput.SetFocus()
    
    def load_messages(self, chat_id):
        # Load messages from local storage
        messages = self.plugin.load_messages_locally(chat_id)
        wx.CallAfter(self.display_messages, messages)
    
    def display_messages(self, messages):
        self.messagesText.Clear()
        
        # Store messages for history navigation
        self.message_history = messages
        self.history_position = -1  # Reset to end (newest)
        
        show_timestamps = self.plugin.config.get('show_timestamps', True)
        
        for m in messages:
            sender = m.get('sender', 'Unknown')
            text = m.get('message', '')
            timestamp = m.get('timestamp', '')
            is_action = m.get('is_action', False)
            
            # Convert to local time
            date_str = self.format_timestamp(timestamp)
            
            # Format based on whether it's an action or regular message and timestamp setting
            if is_action:
                # /me format
                if show_timestamps:
                    self.messagesText.AppendText(f"{sender} {text} ; {date_str}\n")
                else:
                    self.messagesText.AppendText(f"{sender} {text}\n")
            else:
                # Regular format
                if show_timestamps:
                    self.messagesText.AppendText(f"{sender}; {text} ; {date_str}\n")
                else:
                    self.messagesText.AppendText(f"{sender}; {text}\n")
    
    def onInputCharHook(self, e):
        """Handle Page Up/Page Down for message history navigation from input box"""
        keycode = e.GetKeyCode()
        modifiers = e.GetModifiers()
        
        # ONLY handle plain Page Up/Down and Shift+Page Up/Down
        if keycode == wx.WXK_PAGEUP and (modifiers == wx.MOD_NONE or modifiers == wx.MOD_SHIFT):
            if not self.message_history:
                e.Skip()
                return
            
            # Import speech control
            import speech
            
            # Suppress ALL speech immediately
            speech.setSpeechMode(speech.SpeechMode.off)
            
            # Check if Shift is pressed
            if modifiers == wx.MOD_SHIFT:
                # Shift+Page Up - Jump to OLDEST message (beginning)
                if len(self.message_history) > 0:
                    self.history_position = 0
                    msg = self.message_history[0]
                    # Delay announcement to override any automatic speech - 150ms sweet spot
                    wx.CallLater(150, self._delayed_announce, msg, _("Oldest message"))
            else:
                # Page Up - Go back ONE message (older)
                if self.history_position == -1:
                    self.history_position = len(self.message_history) - 1
                elif self.history_position > 0:
                    self.history_position -= 1
                
                if 0 <= self.history_position < len(self.message_history):
                    msg = self.message_history[self.history_position]
                    # Delay announcement to override any automatic speech - increased delay
                    wx.CallLater(150, self._delayed_announce, msg, None)
                else:
                    # Turn speech back on
                    wx.CallLater(150, lambda: speech.setSpeechMode(speech.SpeechMode.talk))
            
            # DON'T process event
            return
            
        elif keycode == wx.WXK_PAGEDOWN and (modifiers == wx.MOD_NONE or modifiers == wx.MOD_SHIFT):
            if not self.message_history:
                e.Skip()
                return
            
            # Import speech control
            import speech
            
            # Suppress ALL speech immediately
            speech.setSpeechMode(speech.SpeechMode.off)
            
            # Check if Shift is pressed
            if modifiers == wx.MOD_SHIFT:
                # Shift+Page Down - Jump to NEWEST message (end)
                if len(self.message_history) > 0:
                    self.history_position = len(self.message_history) - 1
                    msg = self.message_history[-1]
                    # Delay announcement to override any automatic speech - increased delay
                    wx.CallLater(150, self._delayed_announce, msg, _("Newest message"))
            else:
                # Page Down - Go forward ONE message (newer)
                if self.history_position != -1 and self.history_position < len(self.message_history) - 1:
                    self.history_position += 1
                    msg = self.message_history[self.history_position]
                    # Delay announcement to override any automatic speech - increased delay
                    wx.CallLater(150, self._delayed_announce, msg, None)
                elif self.history_position == len(self.message_history) - 1:
                    # Already at newest
                    def announce_newest():
                        import speech
                        speech.setSpeechMode(speech.SpeechMode.talk)
                        ui.message(_("At newest message"))
                    wx.CallLater(150, announce_newest)
                    self.history_position = -1
                else:
                    # Turn speech back on
                    wx.CallLater(150, lambda: speech.setSpeechMode(speech.SpeechMode.talk))
            
            # DON'T process event
            return
        else:
            # Let all other keys work normally
            e.Skip()
    
    def _delayed_announce(self, message, prefix=None):
        """Announce message after delay - ensures speech is clean"""
        import speech
        # Turn speech back on
        speech.setSpeechMode(speech.SpeechMode.talk)
        # Announce our message
        self.announce_message(message, prefix if prefix else "")
    
    def onHistoryCharHook(self, e):
        """Handle Page Up/Page Down for message history navigation from chat history"""
        # Just call the same handler as input box
        self.onInputCharHook(e)
    
    def announce_message(self, message, prefix=""):
        """Announce a message from history"""
        sender = message.get('sender', 'Unknown')
        text = message.get('message', '')
        is_action = message.get('is_action', False)
        
        # Format message
        if is_action:
            formatted = f"{sender} {text}"
        else:
            formatted = f"{sender}: {text}"
        
        # Add prefix if provided
        if prefix:
            formatted = f"{prefix}. {formatted}"
        
        # Speak it
        ui.message(formatted)
    
    def onSendMessage(self, e):
        if not self.current_chat: return
        msg = self.messageInput.GetValue().strip()
        if not msg: return
        
        # Check for /me command
        is_action = False
        if msg.startswith('/me '):
            is_action = True
            msg = msg[4:]  # Remove '/me ' prefix
        
        self.plugin.send_message(self.current_chat, msg, is_action)
        self.messageInput.Clear()
    
    def on_new_message(self, chat_id, message):
        if chat_id == self.current_chat:
            sender = message.get('sender', 'Unknown')
            text = message.get('message', '')
            is_action = message.get('is_action', False)
            is_own_message = (sender == self.plugin.config.get('username'))
            
            # Add this message to history so Page Up/Down can navigate to it
            # Create a proper message object with timestamp
            new_message = {
                'sender': sender,
                'message': text,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'is_action': is_action
            }
            self.message_history.append(new_message)
            # Reset position to end (newest)
            self.history_position = -1
            
            # Use current PC time instead of server timestamp
            date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            show_timestamps = self.plugin.config.get('show_timestamps', True)
            
            # Suppress auto-read for ALL messages, we'll manually speak them
            import speech
            speech.setSpeechMode(speech.SpeechMode.off)
            
            # Format and append based on whether it's an action or regular message and timestamp setting
            if is_action:
                if show_timestamps:
                    self.messagesText.AppendText(f"{sender} {text} ; {date_str}\n")
                else:
                    self.messagesText.AppendText(f"{sender} {text}\n")
            else:
                if show_timestamps:
                    self.messagesText.AppendText(f"{sender}; {text} ; {date_str}\n")
                else:
                    self.messagesText.AppendText(f"{sender}; {text}\n")
            
            # Re-enable speech immediately
            speech.setSpeechMode(speech.SpeechMode.talk)
            
            # Check if this chat is muted
            is_muted = chat_id in self.plugin.config.get('muted_chats', [])
            
            # Manually speak the message based on settings (only if not muted)
            if not is_muted:
                if is_own_message:
                    # It's our message - only speak if enabled
                    if self.plugin.config.get('speak_message_sent', False):
                        if is_action:
                            ui.message(f"{sender} {text}")
                        else:
                            ui.message(f"{sender}; {text}")
                else:
                    # It's someone else's message - speak if in window and enabled
                    if self.plugin.config.get('read_messages_aloud', True):
                        if is_action:
                            ui.message(f"{sender} {text}")
                        else:
                            ui.message(f"{sender}; {text}")
        
        self.refresh_chats()
    
    def onNewChat(self, e):
        if not self.plugin.friends:
            ui.message(_("No friends. Add friends first."))
            return
        
        choices = [_("Private Chat"), _("Group Chat")]
        dlg = wx.SingleChoiceDialog(self, _("What type of chat?"), _("New Chat"), choices)
        
        if dlg.ShowModal() == wx.ID_OK:
            choice = dlg.GetSelection()
            dlg.Destroy()
            
            if choice == 0:
                self.create_private_chat()
            else:
                self.create_group_chat()
        else:
            dlg.Destroy()
    
    def create_private_chat(self):
        dlg = wx.SingleChoiceDialog(self, _("Select friend to chat with:"), _("New Private Chat"), [f['username'] for f in self.plugin.friends])
        if dlg.ShowModal() == wx.ID_OK:
            sel = dlg.GetSelection()
            friend = self.plugin.friends[sel]['username']
            self.plugin.create_chat([self.plugin.config['username'], friend], self.on_chat_created, chat_type='private')
        dlg.Destroy()
    
    def create_group_chat(self):
        CreateGroupDialog(self, self.plugin).ShowModal()
    
    def on_chat_created(self, chat_id):
        ui.message(_("Chat opened"))
        self.refresh_chats()
        
        # Get sorted chat list to find the index
        sorted_chats = sorted(
            self.plugin.chats.items(),
            key=lambda x: x[1].get('last_message_time', ''),
            reverse=True
        )
        
        # Find the chat in sorted list
        for idx, (cid, c) in enumerate(sorted_chats):
            if cid == chat_id:
                self.chatsList.SetSelection(idx)
                self.onChatSelect(None)
                break
    
    def onDeleteChat(self, e):
        sel = self.chatsList.GetSelection()
        if sel == wx.NOT_FOUND or not self.plugin.chats: return ui.message(_("Select chat"))
        
        # Get sorted chat list to match display order
        sorted_chats = sorted(
            self.plugin.chats.items(),
            key=lambda x: x[1].get('last_message_time', ''),
            reverse=True
        )
        
        if sel >= len(sorted_chats): return
        chat_id, chat = sorted_chats[sel]
        
        dlg = wx.MessageDialog(self, _("Delete this chat?"), _("Confirm"), wx.YES_NO | wx.ICON_QUESTION)
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.delete_chat(chat_id)
            self.current_chat = None
            self.messagesText.Clear()
            self.chatTitle.SetLabel("")
            self.rightPanel.Hide()
            self.Layout()
        dlg.Destroy()
    

    
    def onChatsListRightClick(self, e):
        sel = self.chatsList.GetSelection()
        if sel == wx.NOT_FOUND or not self.plugin.chats:
            return
        
        sorted_chats = sorted(self.plugin.chats.items(), key=lambda x: x[1].get('last_message_time', ''), reverse=True)
        if sel >= len(sorted_chats):
            return
        
        chat_id, chat = sorted_chats[sel]
        chat_type = chat.get('type', 'private')
        is_muted = chat_id in self.plugin.config.get('muted_chats', [])
        
        menu = wx.Menu()
        
        # Mute/Unmute option (for all chat types)
        if is_muted:
            mute_item = menu.Append(wx.ID_ANY, _("Unmute Chat"))
            self.Bind(wx.EVT_MENU, lambda e: self.toggle_mute(chat_id), mute_item)
        else:
            mute_item = menu.Append(wx.ID_ANY, _("Mute Chat"))
            self.Bind(wx.EVT_MENU, lambda e: self.toggle_mute(chat_id), mute_item)
        
        menu.AppendSeparator()
        
        if chat_type == 'group':
            is_admin = chat.get('admin') == self.plugin.config.get('username')
            
            members_item = menu.Append(wx.ID_ANY, _("View Members"))
            self.Bind(wx.EVT_MENU, lambda e: self.on_view_members(chat_id), members_item)
            
            if is_admin:
                manage_item = menu.Append(wx.ID_ANY, _("Manage Group (Admin)"))
                self.Bind(wx.EVT_MENU, lambda e: self.on_manage_group(chat_id), manage_item)
                
                menu.AppendSeparator()
                
                delete_all_item = menu.Append(wx.ID_ANY, _("Delete Group for Everyone (Admin)"))
                self.Bind(wx.EVT_MENU, lambda e: self.on_delete_group_all(chat_id), delete_all_item)
            
            menu.AppendSeparator()
            delete_local_item = menu.Append(wx.ID_ANY, _("Remove from My List"))
            self.Bind(wx.EVT_MENU, lambda e: self.onDeleteChat(None), delete_local_item)
        else:
            delete_item = menu.Append(wx.ID_ANY, _("Delete Chat"))
            self.Bind(wx.EVT_MENU, lambda e: self.onDeleteChat(None), delete_item)
        
        self.PopupMenu(menu)
        menu.Destroy()
    

    
    def toggle_mute(self, chat_id):
        """Toggle mute status for a chat"""
        muted_chats = self.plugin.config.get('muted_chats', [])
        
        if chat_id in muted_chats:
            # Unmute
            muted_chats.remove(chat_id)
            self.plugin.config['muted_chats'] = muted_chats
            self.plugin.saveConfig()
            ui.message(_("Chat unmuted"))
        else:
            # Mute
            muted_chats.append(chat_id)
            self.plugin.config['muted_chats'] = muted_chats
            self.plugin.saveConfig()
            ui.message(_("Chat muted - no sounds or speech notifications"))
        
        # Refresh chat list to update display
        self.refresh_chats()

    def onChatsListContextMenu(self, e):
        """Handle context menu (Application key)"""
        self.onChatsListRightClick(e)
    def on_view_members(self, chat_id):
        chat = self.plugin.chats.get(chat_id)
        if not chat:
            return
        
        participants = chat.get('participants', [])
        admin = chat.get('admin', '')
        group_name = chat.get('name', 'Group')
        
        member_list = []
        for p in participants:
            if p == admin:
                member_list.append(f"{p} (Admin)")
            else:
                member_list.append(p)
        
        members_text = "\n".join(member_list)
        dlg = wx.MessageDialog(self, _("Members of {name}:\n\n{members}").format(name=group_name, members=members_text), _("Group Members"), wx.OK | wx.ICON_INFORMATION)
        dlg.ShowModal()
        dlg.Destroy()
    
    def on_manage_group(self, chat_id):
        """Open comprehensive group management dialog"""
        ManageGroupDialog(self, self.plugin, chat_id).ShowModal()
    
    def on_delete_group_all(self, chat_id):
        chat = self.plugin.chats.get(chat_id)
        if not chat:
            return
        
        group_name = chat.get('name', 'this group')
        
        dlg = wx.MessageDialog(self, _("Delete '{name}' for EVERYONE?\nThis cannot be undone!").format(name=group_name), _("Delete Group"), wx.YES_NO | wx.ICON_WARNING)
        
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.delete_group(chat_id, callback=lambda: self.refresh_chats())
        dlg.Destroy()

    def onBack(self, e):
        """Go back to the chat list - hide the chat panel"""
        self.rightPanel.Hide()
        self.Layout()
        self.chatsList.SetFocus()
    
    def onManageFriends(self, e):
        if not self.plugin.connected: return ui.message(_("Not connected"))
        FriendsDialog(self, self.plugin).ShowModal()
    
    def onSettings(self, e): SettingsDialog(self, self.plugin).ShowModal()
    
    def onAccount(self, e): AccountDialog(self, self.plugin).ShowModal()
    
    def onConnect(self):
        """Handle connect menu item - check if already connected"""
        if not self.plugin.connected:
            self.plugin.manual_disconnect = False
            wx.CallAfter(self.plugin.connect)
        else:
            ui.message(_("Connected"))
    
    def onDisconnect(self):
        """Handle disconnect menu item - check if already disconnected"""
        if self.plugin.connected:
            self.plugin.manual_disconnect = True
            wx.CallAfter(self.plugin.disconnect)
        else:
            ui.message(_("Not connected"))
    
    def onClose(self, e):
        self.Hide()
        e.Veto()


class CreateGroupDialog(wx.Dialog):
    def __init__(self, parent, plugin):
        super().__init__(parent, title=_("Create Group Chat"), size=(500, 400))
        self.plugin = plugin
        self.Bind(wx.EVT_CHAR_HOOK, self.onKeyPress)
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        sizer.Add(wx.StaticText(self, label=_("Group Name:")), flag=wx.ALL, border=5)
        self.nameText = wx.TextCtrl(self)
        sizer.Add(self.nameText, flag=wx.ALL|wx.EXPAND, border=5)
        
        sizer.Add(wx.StaticText(self, label=_("Select Members (Space to toggle):")), flag=wx.ALL, border=5)
        
        self.membersList = wx.CheckListBox(self, choices=[f['username'] for f in plugin.friends])
        self.membersList.Bind(wx.EVT_CHECKLISTBOX, self.onMemberToggle)
        self.membersList.Bind(wx.EVT_LISTBOX, self.onMemberSelect)
        self.membersList.Bind(wx.EVT_CHAR_HOOK, self.onListKeyPress)
        sizer.Add(self.membersList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        selectAllBtn = wx.Button(self, label=_("Select All"))
        selectAllBtn.Bind(wx.EVT_BUTTON, self.onSelectAll)
        btnSizer.Add(selectAllBtn, flag=wx.ALL, border=5)
        
        deselectAllBtn = wx.Button(self, label=_("Deselect All"))
        deselectAllBtn.Bind(wx.EVT_BUTTON, self.onDeselectAll)
        btnSizer.Add(deselectAllBtn, flag=wx.ALL, border=5)
        sizer.Add(btnSizer, flag=wx.ALIGN_CENTER)
        
        btnSizer2 = wx.BoxSizer(wx.HORIZONTAL)
        createBtn = wx.Button(self, label=_("Create Group"))
        createBtn.Bind(wx.EVT_BUTTON, self.onCreate)
        btnSizer2.Add(createBtn, flag=wx.ALL, border=5)
        
        cancelBtn = wx.Button(self, label=_("Cancel"))
        cancelBtn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        btnSizer2.Add(cancelBtn, flag=wx.ALL, border=5)
        sizer.Add(btnSizer2, flag=wx.ALIGN_CENTER|wx.ALL, border=10)
        
        self.SetSizer(sizer)
        self.Center()
    
    def onKeyPress(self, e):
        if e.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        else:
            e.Skip()
    
    def onListKeyPress(self, e):
        key = e.GetKeyCode()
        if key == wx.WXK_SPACE:
            # Toggle current item
            sel = self.membersList.GetSelection()
            if sel != wx.NOT_FOUND:
                current = self.membersList.IsChecked(sel)
                self.membersList.Check(sel, not current)
                self.announceSelection(sel)
        else:
            e.Skip()
    
    def onMemberSelect(self, e):
        # Announce status when navigating
        sel = self.membersList.GetSelection()
        if sel != wx.NOT_FOUND:
            self.announceSelection(sel)
    
    def onMemberToggle(self, e):
        # Announce when checkbox is toggled
        sel = e.GetSelection()
        self.announceSelection(sel)
    
    def announceSelection(self, index):
        if index != wx.NOT_FOUND:
            username = self.plugin.friends[index]['username']
            checked = self.membersList.IsChecked(index)
            status = "selected" if checked else "not selected"
            ui.message(f"{username} {status}")
    
    def onSelectAll(self, e):
        for i in range(self.membersList.GetCount()):
            self.membersList.Check(i, True)
        ui.message(f"All {self.membersList.GetCount()} members selected")
    
    def onDeselectAll(self, e):
        for i in range(self.membersList.GetCount()):
            self.membersList.Check(i, False)
        ui.message(_("All members deselected"))
    
    def onCreate(self, e):
        group_name = self.nameText.GetValue().strip()
        
        if not group_name:
            ui.message(_("Enter a group name"))
            return
        
        selected_members = []
        for i in range(self.membersList.GetCount()):
            if self.membersList.IsChecked(i):
                selected_members.append(self.plugin.friends[i]['username'])
        
        if len(selected_members) < 1:
            ui.message(_("Select at least one member"))
            return
        
        participants = [self.plugin.config.get('username')] + selected_members
        
        self.plugin.create_chat(participants, callback=lambda chat_id: self.on_group_created(chat_id), chat_type='group', group_name=group_name)
        
        self.Close()
    
    def on_group_created(self, chat_id):
        ui.message(_("Group created"))
        if self.GetParent():
            self.GetParent().refresh_chats()


class ManageGroupDialog(wx.Dialog):
    """Comprehensive group management dialog for admins"""
    
    def __init__(self, parent, plugin, chat_id):
        self.plugin = plugin
        self.chat_id = chat_id
        self.chat = plugin.chats.get(chat_id, {})
        
        group_name = self.chat.get('name', 'Group')
        super().__init__(parent, title=_("Manage Group: {name}").format(name=group_name), size=(600, 500))
        
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        
        # Group name section
        nameSizer = wx.BoxSizer(wx.HORIZONTAL)
        nameSizer.Add(wx.StaticText(self, label=_("Group Name:")), flag=wx.ALL|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.nameText = wx.TextCtrl(self, value=group_name)
        nameSizer.Add(self.nameText, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        renameBtn = wx.Button(self, label=_("Rename"))
        renameBtn.Bind(wx.EVT_BUTTON, self.onRename)
        nameSizer.Add(renameBtn, flag=wx.ALL, border=5)
        mainSizer.Add(nameSizer, flag=wx.EXPAND|wx.ALL, border=5)
        
        mainSizer.Add(wx.StaticLine(self), flag=wx.EXPAND|wx.ALL, border=5)
        
        # Members section
        mainSizer.Add(wx.StaticText(self, label=_("Group Members (Arrow keys to navigate):")), flag=wx.ALL, border=5)
        
        # Member list
        self.membersList = wx.ListBox(self)
        self.membersList.Bind(wx.EVT_LISTBOX, self.onMemberSelect)
        self.membersList.Bind(wx.EVT_CHAR_HOOK, self.onMembersKeyPress)
        mainSizer.Add(self.membersList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Member action buttons
        memberBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.addBtn = wx.Button(self, label=_("Add Member"))
        self.addBtn.Bind(wx.EVT_BUTTON, self.onAddMember)
        memberBtnSizer.Add(self.addBtn, flag=wx.ALL, border=5)
        
        self.removeBtn = wx.Button(self, label=_("Remove Selected Member"))
        self.removeBtn.Bind(wx.EVT_BUTTON, self.onRemoveMember)
        memberBtnSizer.Add(self.removeBtn, flag=wx.ALL, border=5)
        
        self.promoteBtn = wx.Button(self, label=_("Make Admin (Transfer)"))
        self.promoteBtn.Bind(wx.EVT_BUTTON, self.onTransferAdmin)
        memberBtnSizer.Add(self.promoteBtn, flag=wx.ALL, border=5)
        
        mainSizer.Add(memberBtnSizer, flag=wx.ALIGN_CENTER)
        
        mainSizer.Add(wx.StaticLine(self), flag=wx.EXPAND|wx.ALL, border=5)
        
        # Bottom buttons
        bottomBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        
        refreshBtn = wx.Button(self, label=_("Refresh"))
        refreshBtn.Bind(wx.EVT_BUTTON, lambda e: self.refreshMembers())
        bottomBtnSizer.Add(refreshBtn, flag=wx.ALL, border=5)
        
        closeBtn = wx.Button(self, label=_("Close"))
        closeBtn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        bottomBtnSizer.Add(closeBtn, flag=wx.ALL, border=5)
        
        mainSizer.Add(bottomBtnSizer, flag=wx.ALIGN_CENTER|wx.ALL, border=10)
        
        self.SetSizer(mainSizer)
        self.Center()
        
        # Load members
        self.refreshMembers()
    
    def refreshMembers(self):
        """Refresh member list from current chat data"""
        self.chat = self.plugin.chats.get(self.chat_id, {})
        participants = self.chat.get('participants', [])
        admin = self.chat.get('admin', '')
        
        self.membersList.Clear()
        for p in participants:
            if p == admin:
                self.membersList.Append(f"{p} (Admin)")
            else:
                self.membersList.Append(p)
        
        # Announce count
        ui.message(_("{count} members in group").format(count=len(participants)))
    
    def onMemberSelect(self, e):
        """Announce member when selected"""
        sel = self.membersList.GetSelection()
        if sel != wx.NOT_FOUND:
            member = self.membersList.GetString(sel)
            ui.message(member)
    
    def onMembersKeyPress(self, e):
        """Handle keyboard shortcuts in member list"""
        key = e.GetKeyCode()
        
        if key == wx.WXK_DELETE:
            # Delete key removes member
            self.onRemoveMember(None)
        elif key == wx.WXK_INSERT:
            # Insert key adds member
            self.onAddMember(None)
        else:
            e.Skip()
    
    def onRename(self, e):
        """Rename the group"""
        new_name = self.nameText.GetValue().strip()
        current_name = self.chat.get('name', '')
        
        if not new_name:
            ui.message(_("Enter a group name"))
            self.nameText.SetFocus()
            return
        
        if new_name == current_name:
            ui.message(_("Name unchanged"))
            return
        
        # Confirm rename
        dlg = wx.MessageDialog(
            self,
            _("Rename group to '{name}'?").format(name=new_name),
            _("Confirm Rename"),
            wx.YES_NO | wx.ICON_QUESTION
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.rename_group(self.chat_id, new_name, callback=lambda: self.onRenameComplete(new_name))
        dlg.Destroy()
    
    def onRenameComplete(self, new_name):
        """Called after successful rename"""
        self.SetTitle(_("Manage Group: {name}").format(name=new_name))
        ui.message(_("Renamed to {name}").format(name=new_name))
        if self.GetParent():
            self.GetParent().refresh_chats()
    
    def onAddMember(self, e):
        """Add a member to the group"""
        current_members = self.chat.get('participants', [])
        available = [f['username'] for f in self.plugin.friends if f['username'] not in current_members]
        
        if not available:
            ui.message(_("No friends available to add"))
            return
        
        dlg = wx.SingleChoiceDialog(
            self,
            _("Select friend to add (Enter to confirm):"),
            _("Add Group Member"),
            available
        )
        
        if dlg.ShowModal() == wx.ID_OK:
            username = available[dlg.GetSelection()]
            self.plugin.add_group_member(
                self.chat_id,
                username,
                callback=lambda: self.onMemberAdded(username)
            )
        dlg.Destroy()
    
    def onMemberAdded(self, username):
        """Called after member is added"""
        ui.message(_("Added {user}").format(user=username))
        # Reload chat data and refresh
        wx.CallLater(500, self.plugin.load_chats)
        wx.CallLater(700, self.refreshMembers)
    
    def onRemoveMember(self, e):
        """Remove selected member from group"""
        sel = self.membersList.GetSelection()
        if sel == wx.NOT_FOUND:
            ui.message(_("Select a member first"))
            return
        
        participants = self.chat.get('participants', [])
        admin = self.chat.get('admin', '')
        
        if sel >= len(participants):
            return
        
        username = participants[sel]
        
        # Can't remove admin
        if username == admin:
            ui.message(_("Cannot remove admin"))
            return
        
        # Can't remove yourself
        if username == self.plugin.config.get('username'):
            ui.message(_("Cannot remove yourself"))
            return
        
        # Confirm removal
        dlg = wx.MessageDialog(
            self,
            f"Remove {username} from group?",
            "Confirm Removal",
            wx.YES_NO | wx.ICON_WARNING
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.remove_group_member(
                self.chat_id,
                username,
                callback=lambda: self.onMemberRemoved(username)
            )
        dlg.Destroy()
    
    def onMemberRemoved(self, username):
        """Called after member is removed"""
        ui.message(f"Removed {username}")
        # Reload chat data and refresh
        wx.CallLater(500, self.plugin.load_chats)
        wx.CallLater(700, self.refreshMembers)


    
    def onTransferAdmin(self, e):
        """Transfer admin rights to another member"""
        sel = self.membersList.GetSelection()
        if sel == wx.NOT_FOUND:
            ui.message(_("Select a member first"))
            return
        
        participants = self.chat.get('participants', [])
        admin = self.chat.get('admin', '')
        
        if sel >= len(participants):
            return
        
        username = participants[sel]
        
        # Can't transfer to yourself
        if username == self.plugin.config.get('username'):
            ui.message(_("You are already admin"))
            return
        
        # Confirm transfer
        dlg = wx.MessageDialog(
            self,
            _("Transfer admin rights to {user}?\n\nYou will no longer be admin!").format(user=username),
            _("Confirm Transfer"),
            wx.YES_NO | wx.ICON_WARNING
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.transfer_admin(
                self.chat_id,
                username,
                callback=lambda: self.onAdminTransferred(username)
            )
        dlg.Destroy()
    
    def onAdminTransferred(self, new_admin):
        """Called after admin is transferred"""
        ui.message(f"{new_admin} is now admin. You are no longer admin.")
        # Close dialog since we're not admin anymore
        wx.CallLater(1000, self.Close)


class FriendsDialog(wx.Dialog):
    def __init__(self, parent, plugin):
        super().__init__(parent, title=_("Friends"), size=(600, 500))
        self.plugin = plugin
        self.pending_requests = []
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self)
        friendsPanel = wx.Panel(self.notebook)
        friendsSizer = wx.BoxSizer(wx.VERTICAL)
        friendsSizer.Add(wx.StaticText(friendsPanel, label=_("My Friends")), flag=wx.ALL, border=5)
        self.friendsList = wx.ListCtrl(friendsPanel, style=wx.LC_REPORT)
        self.friendsList.InsertColumn(0, _("Username"), width=250)
        self.friendsList.InsertColumn(1, _("Status"), width=100)
        friendsSizer.Add(self.friendsList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        friendsBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        deleteBtn = wx.Button(friendsPanel, label=_("&Delete Friend"))
        deleteBtn.Bind(wx.EVT_BUTTON, self.onDeleteFriend)
        friendsBtnSizer.Add(deleteBtn, flag=wx.ALL, border=5)
        friendsSizer.Add(friendsBtnSizer, flag=wx.ALIGN_CENTER)
        friendsPanel.SetSizer(friendsSizer)
        requestsPanel = wx.Panel(self.notebook)
        requestsSizer = wx.BoxSizer(wx.VERTICAL)
        requestsSizer.Add(wx.StaticText(requestsPanel, label=_("Friend Requests")), flag=wx.ALL, border=5)
        self.requestsList = wx.ListBox(requestsPanel)
        requestsSizer.Add(self.requestsList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        reqBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in [(_("&Accept"), self.onAccept), (_("&Reject"), self.onReject)]:
            btn = wx.Button(requestsPanel, label=label)
            btn.Bind(wx.EVT_BUTTON, handler)
            reqBtnSizer.Add(btn, flag=wx.ALL, border=5)
        requestsSizer.Add(reqBtnSizer, flag=wx.ALIGN_CENTER)
        requestsPanel.SetSizer(requestsSizer)
        self.notebook.AddPage(friendsPanel, _("My Friends"))
        self.notebook.AddPage(requestsPanel, _("Requests"))
        mainSizer.Add(self.notebook, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        # REMOVED THE CLOSE BUTTON - Only Add Friend and Refresh buttons remain
        for label, handler in [(_("&Add Friend"), self.onAdd), (_("&Refresh"), self.onRefresh)]:
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, handler)
            btnSizer.Add(btn, flag=wx.ALL, border=5)
        mainSizer.Add(btnSizer, flag=wx.ALIGN_CENTER)
        self.SetSizer(mainSizer)
        self.loadFriendsData()
        self.Center()
    
    def loadFriendsData(self):
        def load():
            try:
                resp = requests.get(f'{self.plugin.config["server_url"]}/api/friends', headers={'Authorization': f'Bearer {self.plugin.token}'}, timeout=10)
                if resp.status_code == 200:
                    d = resp.json()
                    wx.CallAfter(self.displayFriends, d.get('friends', []))
                    wx.CallAfter(self.displayRequests, d.get('pending_incoming', []), d.get('pending_outgoing', []))
            except: pass
        threading.Thread(target=load, daemon=True).start()
    
    def displayFriends(self, friends):
        self.friendsList.DeleteAllItems()
        if not friends: self.friendsList.InsertItem(0, "No friends")
        else:
            for f in friends:
                idx = self.friendsList.InsertItem(self.friendsList.GetItemCount(), f['username'])
                self.friendsList.SetItem(idx, 1, _('online') if f.get('status') == 'online' else _('offline'))
    
    def displayRequests(self, incoming, outgoing):
        self.requestsList.Clear()
        self.pending_requests = incoming
        if not incoming and not outgoing: self.requestsList.Append(_("No requests"))
        else:
            if incoming:
                # Incoming requests shown with (Incoming) suffix, no header needed
                for u in incoming: self.requestsList.Append(f"{u}")
            if outgoing:
                if incoming: self.requestsList.Append("")
                # Outgoing shown with (waiting), no header
                for u in outgoing: self.requestsList.Append(f"{u} ({_('waiting')})")
        self.notebook.SetPageText(1, _("Requests ({count})").format(count=len(incoming)) if incoming else _("Requests"))
    
    def onAccept(self, e):
        sel = self.requestsList.GetSelection()
        if sel == wx.NOT_FOUND: return ui.message(_("Select request"))
        txt = self.requestsList.GetString(sel).strip()
        if txt.startswith("===") or "No" in txt or not txt: return ui.message(_("Select incoming"))
        username = txt.split()[0]
        if username not in self.pending_requests: return ui.message(_("Invalid"))
        ui.message(_("Accepting {user}...").format(user=username))
        def accept():
            try:
                resp = requests.post(f'{self.plugin.config["server_url"]}/api/friends/accept', headers={'Authorization': f'Bearer {self.plugin.token}'}, json={'username': username}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: (ui.message(f"Accepted!"), self.plugin.playSound('user_online'), self.loadFriendsData(), self.plugin.load_friends()))
            except: wx.CallAfter(lambda: ui.message(_("Error")))
        threading.Thread(target=accept, daemon=True).start()
    
    def onReject(self, e): ui.message(_("Coming soon"))
    
    def onRefresh(self, e):
        self.loadFriendsData()
        ui.message(_("Refreshing..."))
    
    def onDeleteFriend(self, e):
        sel = self.friendsList.GetFirstSelected()
        if sel == -1: return ui.message(_("Select friend"))
        username = self.friendsList.GetItemText(sel, 0)
        if not username or username == "No friends": return
        dlg = wx.MessageDialog(self, f"Delete {username}?", "Confirm", wx.YES_NO | wx.ICON_QUESTION)
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.delete_friend(username)
            wx.CallLater(1000, self.loadFriendsData)
        dlg.Destroy()
    
    def onAdd(self, e):
        dlg = wx.TextEntryDialog(self, _("Friend's username:"), _("Add Friend"))
        if dlg.ShowModal() == wx.ID_OK:
            username = dlg.GetValue().strip()
            if username:
                def add():
                    try:
                        resp = requests.post(f'{self.plugin.config["server_url"]}/api/friends/add', headers={'Authorization': f'Bearer {self.plugin.token}'}, json={'username': username}, timeout=10)
                        if resp.status_code == 200: wx.CallAfter(lambda: (ui.message(_("Request sent!")), self.loadFriendsData()))
                        else: wx.CallAfter(lambda: ui.message(_("Error")))
                    except: wx.CallAfter(lambda: ui.message(_("Connection error")))
                threading.Thread(target=add, daemon=True).start()
        dlg.Destroy()

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, plugin):
        super().__init__(parent, title=_("Settings"), size=(600, 600))
        self.plugin = plugin
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        
        # Create notebook for tabs
        self.notebook = wx.Notebook(self)
        
        # General Tab
        generalPanel = wx.Panel(self.notebook)
        generalSizer = wx.BoxSizer(wx.VERTICAL)
        
        # Local message saving
        generalSizer.Add(wx.StaticText(generalPanel, label=_("Message History:")), flag=wx.ALL, border=5)
        self.saveLocalCheck = wx.CheckBox(generalPanel, label=_("Save chat messages locally"))
        self.saveLocalCheck.SetValue(plugin.config.get("save_messages_locally", True))
        generalSizer.Add(self.saveLocalCheck, flag=wx.ALL, border=5)
        
        # Messages folder selection
        folderSizer = wx.BoxSizer(wx.HORIZONTAL)
        generalSizer.Add(wx.StaticText(generalPanel, label=_("Messages folder:")), flag=wx.ALL, border=5)
        self.messagesFolderText = wx.TextCtrl(generalPanel, value=plugin.config.get("messages_folder", os.path.join(os.path.expanduser("~"), "NVDA Chat Messages")))
        folderSizer.Add(self.messagesFolderText, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        browseBtn = wx.Button(generalPanel, label=_("&Browse..."))
        browseBtn.Bind(wx.EVT_BUTTON, self.onBrowseFolder)
        folderSizer.Add(browseBtn, flag=wx.ALL, border=5)
        generalSizer.Add(folderSizer, flag=wx.EXPAND)
        
        # Logging section
        generalSizer.Add(wx.StaticLine(generalPanel), flag=wx.ALL|wx.EXPAND, border=10)
        generalSizer.Add(wx.StaticText(generalPanel, label=_("Logging:")), flag=wx.ALL, border=5)
        logBtn = wx.Button(generalPanel, label=_("&View NVDA Log"))
        logBtn.Bind(wx.EVT_BUTTON, self.onViewLog)
        generalSizer.Add(logBtn, flag=wx.ALL, border=5)
        
        # Updates section
        generalSizer.Add(wx.StaticLine(generalPanel), flag=wx.ALL|wx.EXPAND, border=10)
        generalSizer.Add(wx.StaticText(generalPanel, label=_("Updates:")), flag=wx.ALL, border=5)
        updateBtn = wx.Button(generalPanel, label=_("&Check for Updates Now"))
        updateBtn.Bind(wx.EVT_BUTTON, self.onCheckUpdates)
        generalSizer.Add(updateBtn, flag=wx.ALL, border=5)
        
        # Auto-check on startup checkbox
        self.autoCheckUpdatesCheck = wx.CheckBox(generalPanel, label=_("Check for updates automatically on startup"))
        self.autoCheckUpdatesCheck.SetValue(plugin.config.get("check_updates_on_startup", True))
        generalSizer.Add(self.autoCheckUpdatesCheck, flag=wx.ALL, border=5)
        
        # Display section
        generalSizer.Add(wx.StaticLine(generalPanel), flag=wx.ALL|wx.EXPAND, border=10)
        generalSizer.Add(wx.StaticText(generalPanel, label=_("Display:")), flag=wx.ALL, border=5)
        
        # Show timestamps checkbox
        self.showTimestampsCheck = wx.CheckBox(generalPanel, label=_("Show date and time in messages"))
        self.showTimestampsCheck.SetValue(plugin.config.get("show_timestamps", True))
        generalSizer.Add(self.showTimestampsCheck, flag=wx.ALL, border=5)
        
        generalPanel.SetSizer(generalSizer)
        
        # Sounds Tab
        soundsPanel = wx.Panel(self.notebook)
        soundsSizer = wx.BoxSizer(wx.VERTICAL)
        self.soundCheck = wx.CheckBox(soundsPanel, label=_("Enable all sounds"))
        self.soundCheck.SetValue(plugin.config.get("sound_enabled", True))
        soundsSizer.Add(self.soundCheck, flag=wx.ALL, border=5)
        soundsSizer.Add(wx.StaticLine(soundsPanel), flag=wx.ALL|wx.EXPAND, border=5)
        soundsSizer.Add(wx.StaticText(soundsPanel, label=_("Individual Sound Settings:")), flag=wx.ALL, border=5)
        
        # Individual sound checkboxes
        self.sound_checks = {}
        sound_labels = {
            "sound_message_received": _("Message received"),
            "sound_message_sent": _("Message sent"),
            "sound_user_online": _("User comes online"),
            "sound_user_offline": _("User goes offline"),
            "sound_friend_request": _("Friend request"),
            "sound_error": _("Error"),
            "sound_connected": _("Connected"),
            "sound_disconnected": _("Disconnected")
        }
        for key, label in sound_labels.items():
            check = wx.CheckBox(soundsPanel, label=label)
            check.SetValue(plugin.config.get(key, True))
            self.sound_checks[key] = check
            soundsSizer.Add(check, flag=wx.ALL, border=5)
        soundsPanel.SetSizer(soundsSizer)
        
        # Notifications Tab
        notifPanel = wx.Panel(self.notebook)
        notifSizer = wx.BoxSizer(wx.VERTICAL)
        notifSizer.Add(wx.StaticText(notifPanel, label=_("Speech Notifications:")), flag=wx.ALL, border=5)
        
        self.readMessagesCheck = wx.CheckBox(notifPanel, label=_("Read messages aloud when in chat window"))
        self.readMessagesCheck.SetValue(plugin.config.get("read_messages_aloud", True))
        notifSizer.Add(self.readMessagesCheck, flag=wx.ALL, border=5)
        notifSizer.Add(wx.StaticLine(notifPanel), flag=wx.ALL|wx.EXPAND, border=5)
        
        # Individual speak checkboxes
        self.speak_checks = {}
        speak_labels = {
            "speak_message_received": _("Speak when message received"),
            "speak_message_sent": _("Speak when message sent"),
            "speak_user_online": _("Speak when user comes online"),
            "speak_user_offline": _("Speak when user goes offline"),
            "speak_friend_request": _("Speak friend requests")
        }
        for key, label in speak_labels.items():
            check = wx.CheckBox(notifPanel, label=label)
            check.SetValue(plugin.config.get(key, True))
            self.speak_checks[key] = check
            notifSizer.Add(check, flag=wx.ALL, border=5)
        notifPanel.SetSizer(notifSizer)
        
        # Add tabs to notebook
        self.notebook.AddPage(generalPanel, _("General"))
        self.notebook.AddPage(soundsPanel, _("Sounds"))
        self.notebook.AddPage(notifPanel, _("Notifications"))
        mainSizer.Add(self.notebook, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Buttons
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        saveBtn = wx.Button(self, label=_("&Save"))
        saveBtn.Bind(wx.EVT_BUTTON, self.onSave)
        cancelBtn = wx.Button(self, label=_("&Cancel"))
        cancelBtn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        btnSizer.Add(saveBtn, flag=wx.ALL, border=5)
        btnSizer.Add(cancelBtn, flag=wx.ALL, border=5)
        mainSizer.Add(btnSizer, flag=wx.ALIGN_CENTER|wx.ALL, border=10)
        
        self.SetSizer(mainSizer)
        self.Center()
    
    def onBrowseFolder(self, e):
        """Browse for messages folder"""
        dlg = wx.DirDialog(self, "Choose folder for message history:", 
                          defaultPath=self.messagesFolderText.GetValue(),
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self.messagesFolderText.SetValue(dlg.GetPath())
        dlg.Destroy()
    
    def onViewLog(self, e):
        """Open NVDA log viewer"""
        import subprocess
        import sys
        log_path = os.path.join(os.path.expandvars("%TEMP%"), "nvda.log")
        try:
            subprocess.Popen([sys.executable, "-m", "logViewer", log_path])
        except:
            ui.message(_("Could not open log viewer"))
    
    def onCheckUpdates(self, e):
        """Check for addon updates"""
        self.plugin.check_for_updates(show_no_update=True)
    
    def onSave(self, e):
        # Save general settings (no account settings)
        self.plugin.config.update({
            "sound_enabled": self.soundCheck.GetValue(),
            "read_messages_aloud": self.readMessagesCheck.GetValue(),
            "save_messages_locally": self.saveLocalCheck.GetValue(),
            "messages_folder": self.messagesFolderText.GetValue(),
            "check_updates_on_startup": self.autoCheckUpdatesCheck.GetValue(),
            "show_timestamps": self.showTimestampsCheck.GetValue()
        })
        
        # Save individual sound settings
        for key, check in self.sound_checks.items():
            self.plugin.config[key] = check.GetValue()
        
        # Save individual speak settings
        for key, check in self.speak_checks.items():
            self.plugin.config[key] = check.GetValue()
        
        self.plugin.saveConfig()
        ui.message(_("Saved!"))
        self.Close()

class AccountDialog(wx.Dialog):
    def __init__(self, parent, plugin):
        super().__init__(parent, title=_("Account Settings"), size=(500, 450))
        self.plugin = plugin
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Server settings
        sizer.Add(wx.StaticText(self, label=_("Server URL:")), flag=wx.ALL, border=5)
        self.serverText = wx.TextCtrl(self, value=plugin.config["server_url"])
        sizer.Add(self.serverText, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Account information
        sizer.Add(wx.StaticLine(self), flag=wx.ALL|wx.EXPAND, border=10)
        sizer.Add(wx.StaticText(self, label=_("Account Information:")), flag=wx.ALL, border=5)
        
        sizer.Add(wx.StaticText(self, label=_("Username:")), flag=wx.ALL, border=5)
        self.userText = wx.TextCtrl(self, value=plugin.config["username"])
        sizer.Add(self.userText, flag=wx.ALL|wx.EXPAND, border=5)
        
        sizer.Add(wx.StaticText(self, label=_("Password:")), flag=wx.ALL, border=5)
        self.passText = wx.TextCtrl(self, value=plugin.config["password"], style=wx.TE_PASSWORD)
        sizer.Add(self.passText, flag=wx.ALL|wx.EXPAND, border=5)
        
        sizer.Add(wx.StaticText(self, label=_("Email (optional):")), flag=wx.ALL, border=5)
        self.emailText = wx.TextCtrl(self, value=plugin.config.get("email", ""))
        sizer.Add(self.emailText, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Auto-connect
        self.autoCheck = wx.CheckBox(self, label=_("Auto-connect on startup"))
        self.autoCheck.SetValue(plugin.config.get("auto_connect", False))
        sizer.Add(self.autoCheck, flag=wx.ALL, border=5)
        
        # Buttons
        sizer.Add(wx.StaticLine(self), flag=wx.ALL|wx.EXPAND, border=10)
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        
        registerBtn = wx.Button(self, label=_("&Register New Account"))
        registerBtn.Bind(wx.EVT_BUTTON, self.onRegister)
        btnSizer.Add(registerBtn, flag=wx.ALL, border=5)
        
        saveBtn = wx.Button(self, label=_("&Save"))
        saveBtn.Bind(wx.EVT_BUTTON, self.onSave)
        btnSizer.Add(saveBtn, flag=wx.ALL, border=5)
        
        cancelBtn = wx.Button(self, label=_("&Cancel"))
        cancelBtn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        btnSizer.Add(cancelBtn, flag=wx.ALL, border=5)
        
        sizer.Add(btnSizer, flag=wx.ALIGN_CENTER|wx.ALL, border=10)
        
        self.SetSizer(sizer)
        self.Center()
    
    def onRegister(self, e):
        username = self.userText.GetValue().strip()
        password = self.passText.GetValue().strip()
        email = self.emailText.GetValue().strip()
        server_url = self.serverText.GetValue().strip()
        
        if not username or not password:
            return ui.message(_("Enter username and password"))
        if len(password) < 6:
            return ui.message(_("Password must be 6+ characters"))
        
        ui.message(_("Creating account..."))
        
        def register():
            try:
                resp = requests.post(f'{server_url}/api/auth/register', 
                                    json={'username': username, 'password': password, 'email': email}, 
                                    timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: (ui.message(f"Account created! Welcome {username}"), 
                                         self.plugin.playSound('connected')))
                    self.plugin.config.update({'username': username, 'password': password, 'email': email})
                    self.plugin.saveConfig()
                elif resp.status_code == 409:
                    wx.CallAfter(lambda: ui.message(_("Username taken")))
                else:
                    wx.CallAfter(lambda: ui.message(_("Registration failed")))
            except:
                wx.CallAfter(lambda: ui.message(_("Cannot reach server")))
        
        threading.Thread(target=register, daemon=True).start()
    
    def onSave(self, e):
        self.plugin.config.update({
            "server_url": self.serverText.GetValue(),
            "username": self.userText.GetValue(),
            "password": self.passText.GetValue(),
            "email": self.emailText.GetValue(),
            "auto_connect": self.autoCheck.GetValue()
        })
        self.plugin.saveConfig()
        ui.message(_("Account settings saved!"))
        self.Close()
