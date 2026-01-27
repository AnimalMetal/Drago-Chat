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


# Version info for update checker
ADDON_VERSION = "2.0.0"
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/AnimalMetal/nvda-chat/main/version.json"

addonHandler.initTranslation()

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DEFAULT_CONFIG = {
    "server_url": "http://tt.dragodark.com:8080", 
    "username": "", 
    "password": "", 
    "email": "", 
    "auto_connect": False, 
    "sound_enabled": True, 
    "notifications_enabled": True, 
    "reconnect_attempts": 10, 
    "reconnect_delay": 5,
    # Local message saving
    "save_messages_locally": True,
    "messages_folder": os.path.join(os.path.expanduser("~"), "NVDA Chat Messages"),
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
            wx.CallLater(1000, lambda: ui.message("Error: Libraries missing"))
            return
        self.createMenu()
        self.start_message_processor()
        if self.config.get('auto_connect'):
            wx.CallLater(2000, self.connect)
    
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
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f: return json.load(f)
        except: pass
        return DEFAULT_CONFIG.copy()
    
    def saveConfig(self):
        try:
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
            
            # Play different sound for group vs private messages
            chat = self.chats.get(cid, {})
            is_group = chat.get('type') == 'group'
            if is_group:
                self.playSound('group_message')
            else:
                self.playSound('message_received')
            
            # Check if we're in the chat window and viewing this chat
            in_chat_window = viewing_this_chat
            
            # Speak notification based on settings and location
            speak_setting = 'speak_group_message' if is_group else 'speak_message_received'
            if self.config.get(speak_setting, True):
                if in_chat_window and self.config.get('read_messages_aloud', True):
                    # Read full message if in chat window
                    text = m.get('message', '')
                    if is_group:
                        group_name = chat.get('name', 'Group')
                        ui.message(f"{sender} in {group_name}: {text}")
                    else:
                        ui.message(f"{sender}: {text}")
                elif not in_chat_window:
                    # Just say "Message from X" if outside chat window
                    if is_group:
                        group_name = chat.get('name', 'Group')
                        ui.message(f"{sender} in {group_name}")
                    else:
                        ui.message(f"Message from {sender}")
            
            if self.chat_window: self.chat_window.on_new_message(cid, m)
            
        elif t == 'user_online':
            u = d.get('username')
            self.playSound('user_online')
            if self.config.get('speak_user_online', True):
                ui.message(f"{u} is online")
            for f in self.friends:
                if f['username'] == u: f['status'] = 'online'; break
            if self.chat_window: wx.CallAfter(self.chat_window.refresh_friends)
            
        elif t == 'user_offline':
            u = d.get('username')
            self.playSound('user_offline')
            if self.config.get('speak_user_offline', True):
                ui.message(f"{u} is offline")
            for f in self.friends:
                if f['username'] == u: f['status'] = 'offline'; break
            if self.chat_window: wx.CallAfter(self.chat_window.refresh_friends)
            
        elif t == 'friend_request':
            self.playSound('friend_request')
            if self.config.get('speak_friend_request', True):
                ui.message(f"Friend request from {d.get('from')}")
            self.load_friends()
            
        elif t == 'friend_accepted':
            self.playSound('user_online')
            ui.message(f"{d.get('username')} accepted friend request")
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
        else: ui.message("Connected")
    
    @script(description="Disconnect", category="NVDA Chat")
    def script_disconnect(self, gesture):
        if self.connected: self.manual_disconnect = True; wx.CallAfter(self.disconnect)
        else: ui.message("Not connected")
    
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
                ui.message("Chat window opened")
        except Exception as e:
            import traceback
            ui.message(f"Error opening window: {e}")
            traceback.print_exc()
    
    def connect(self):
        if not self.config.get('username') or not self.config.get('password'):
            ui.message("Configure credentials")
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
                    wx.CallAfter(lambda: (self.playSound('connected'), ui.message("Connected")))
                # Silent reconnection - no beep, no message
                
                self.startWebSocket()
                wx.CallAfter(self.load_friends)
                wx.CallAfter(self.load_chats)
            else: wx.CallAfter(lambda: ui.message("Login failed"))
        except requests.exceptions.Timeout:
            if self.reconnect_count == 0:
                wx.CallAfter(lambda: ui.message("Timeout"))
            if not self.manual_disconnect: self.schedule_reconnect()
        except requests.exceptions.ConnectionError:
            if self.reconnect_count == 0:
                wx.CallAfter(lambda: ui.message("Server unreachable"))
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
        
        # Only make noise if manually disconnected
        if self.manual_disconnect:
            wx.CallAfter(lambda: (self.playSound('disconnected'), ui.message("Disconnected")))
            return
        
        # Connection lost - silently try to reconnect
        # No sounds, no messages during reconnection attempts
        if self.reconnect_count < self.config.get('reconnect_attempts', 3):
            wx.CallAfter(self.schedule_reconnect)
        else:
            # Only notify after all attempts exhausted
            wx.CallAfter(lambda: ui.message("Connection lost. Manual reconnect needed."))
    
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
        
        # Only play sound and announce if not silent
        if not silent:
            self.playSound('disconnected')
            ui.message("Disconnected")
    
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
                if resp.status_code == 200: wx.CallAfter(lambda: (ui.message("Friend deleted"), self.load_friends()))
                else: wx.CallAfter(lambda: ui.message("Error deleting friend"))
            except: wx.CallAfter(lambda: ui.message("Connection error"))
        threading.Thread(target=delete, daemon=True).start()
    
    def delete_chat(self, chat_id):
        if not self.token: return
        def delete():
            try:
                resp = requests.delete(f'{self.config["server_url"]}/api/chats/delete/{chat_id}', headers={'Authorization': f'Bearer {self.token}'}, timeout=10)
                if resp.status_code == 200:
                    if chat_id in self.chats: del self.chats[chat_id]
                    wx.CallAfter(lambda: (ui.message("Chat deleted"), self.load_chats()))
                else: wx.CallAfter(lambda: ui.message("Error deleting chat"))
            except: wx.CallAfter(lambda: ui.message("Connection error"))
        threading.Thread(target=delete, daemon=True).start()
    
    
    # Group management methods
    def add_group_member(self, chat_id, username, callback=None):
        if not self.token: return
        def add():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/chats/group/add-member', headers={'Authorization': f'Bearer {self.token}'}, json={'chat_id': chat_id, 'username': username}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: ui.message(f"Added {username} to group"))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message("Error adding member"))
            except: wx.CallAfter(lambda: ui.message("Connection error"))
        threading.Thread(target=add, daemon=True).start()
    
    def remove_group_member(self, chat_id, username, callback=None):
        if not self.token: return
        def remove():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/chats/group/remove-member', headers={'Authorization': f'Bearer {self.token}'}, json={'chat_id': chat_id, 'username': username}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: ui.message(f"Removed {username} from group"))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message("Error removing member"))
            except: wx.CallAfter(lambda: ui.message("Connection error"))
        threading.Thread(target=remove, daemon=True).start()
    
    def rename_group(self, chat_id, new_name, callback=None):
        if not self.token: return
        def rename():
            try:
                resp = requests.post(f'{self.config["server_url"]}/api/chats/group/rename', headers={'Authorization': f'Bearer {self.token}'}, json={'chat_id': chat_id, 'new_name': new_name}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: ui.message(f"Group renamed to {new_name}"))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message("Error renaming group"))
            except: wx.CallAfter(lambda: ui.message("Connection error"))
        threading.Thread(target=rename, daemon=True).start()
    
    def delete_group(self, chat_id, callback=None):
        if not self.token: return
        def delete():
            try:
                resp = requests.delete(f'{self.config["server_url"]}/api/chats/group/delete/{chat_id}', headers={'Authorization': f'Bearer {self.token}'}, timeout=10)
                if resp.status_code == 200:
                    if chat_id in self.chats: del self.chats[chat_id]
                    wx.CallAfter(lambda: ui.message("Group deleted"))
                    self.load_chats()
                    if callback: wx.CallAfter(callback)
                else: wx.CallAfter(lambda: ui.message("Error deleting group"))
            except: wx.CallAfter(lambda: ui.message("Connection error"))
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
                wx.CallAfter(lambda: ui.message("Error creating chat"))
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
                else: wx.CallAfter(lambda: ui.message("Error transferring admin"))
            except: wx.CallAfter(lambda: ui.message("Connection error"))
        threading.Thread(target=transfer, daemon=True).start()


    
    def check_for_updates(self, silent=False):
        """Check for addon updates from GitHub"""
        def check():
            try:
                resp = requests.get(UPDATE_CHECK_URL, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    latest_version = data.get('version', '0.0.0')
                    download_url = data.get('download_url', '')
                    changelog = data.get('changelog', '')
                    
                    # Compare versions
                    if self.compare_versions(latest_version, ADDON_VERSION) > 0:
                        # New version available
                        wx.CallAfter(self.show_update_dialog, latest_version, download_url, changelog)
                    else:
                        # Up to date
                        if not silent:
                            wx.CallAfter(lambda: ui.message(f"You have the latest version ({ADDON_VERSION})"))
                else:
                    if not silent:
                        wx.CallAfter(lambda: ui.message("Could not check for updates"))
            except Exception as e:
                if not silent:
                    wx.CallAfter(lambda: ui.message(f"Update check failed: {e}"))
        
        threading.Thread(target=check, daemon=True).start()
    
    def compare_versions(self, v1, v2):
        """Compare two version strings. Returns: 1 if v1 > v2, -1 if v1 < v2, 0 if equal"""
        try:
            parts1 = [int(x) for x in v1.split('.')]
            parts2 = [int(x) for x in v2.split('.')]
            
            for i in range(max(len(parts1), len(parts2))):
                p1 = parts1[i] if i < len(parts1) else 0
                p2 = parts2[i] if i < len(parts2) else 0
                
                if p1 > p2:
                    return 1
                elif p1 < p2:
                    return -1
            
            return 0
        except:
            return 0
    
    def show_update_dialog(self, new_version, download_url, changelog):
        """Show update available dialog"""
        message = f"New version available: {new_version}\n"
        message += f"Current version: {ADDON_VERSION}\n\n"
        message += "Changes:\n" + changelog + "\n\n"
        message += "Would you like to download and install the update?"
        
        dlg = wx.MessageDialog(
            None,
            message,
            f"NVDA Chat Update Available",
            wx.YES_NO | wx.ICON_INFORMATION
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            self.download_and_install_update(download_url, new_version)
        dlg.Destroy()
    
    def download_and_install_update(self, url, version):
        """Download and install update"""
        ui.message("Downloading update...")
        
        def download():
            try:
                import tempfile
                import subprocess
                
                # Download file
                resp = requests.get(url, timeout=60)
                if resp.status_code == 200:
                    # Save to temp file
                    temp_file = os.path.join(tempfile.gettempdir(), f"nvda-chat-{version}.nvda-addon")
                    with open(temp_file, 'wb') as f:
                        f.write(resp.content)
                    
                    # Open the addon file (NVDA will handle installation)
                    wx.CallAfter(lambda: ui.message("Download complete. Opening installer..."))
                    wx.CallLater(1000, lambda: os.startfile(temp_file))
                else:
                    wx.CallAfter(lambda: ui.message("Download failed"))
            except Exception as e:
                wx.CallAfter(lambda: ui.message(f"Update failed: {e}"))
        
        threading.Thread(target=download, daemon=True).start()

    def terminate(self):
        self.disconnect(silent=True)  # Silent disconnect on NVDA restart
        try:
            if self.chatMenuItem: self.toolsMenu.Remove(self.chatMenuItem)
        except: pass
        super().terminate()

class ChatWindow(wx.Frame):
    def __init__(self, parent, plugin):
        super().__init__(parent, title="NVDA Chat", size=(800, 600))
        self.plugin = plugin
        self.current_chat = None
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.Bind(wx.EVT_CHAR_HOOK, self.onKeyPress)
        
        panel = wx.Panel(self)
        mainSizer = wx.BoxSizer(wx.HORIZONTAL)
        leftPanel = wx.Panel(panel)
        leftSizer = wx.BoxSizer(wx.VERTICAL)
        leftSizer.Add(wx.StaticText(leftPanel, label="Chats"), flag=wx.ALL, border=5)
        self.chatsList = wx.ListBox(leftPanel, style=wx.LB_SINGLE)
        self.chatsList.Bind(wx.EVT_CHAR_HOOK, self.onChatsListChar)
        self.chatsList.Bind(wx.EVT_RIGHT_DOWN, self.onChatsListRightClick)
        self.chatsList.Bind(wx.EVT_CONTEXT_MENU, self.onChatsListContextMenu)
        leftSizer.Add(self.chatsList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        leftBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in [("&New Chat", self.onNewChat), ("&Delete", self.onDeleteChat), ("&Account", self.onAccount)]:
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
        backBtn = wx.Button(self.rightPanel, label="&Back")
        backBtn.Bind(wx.EVT_BUTTON, self.onBack)
        topSizer.Add(backBtn, flag=wx.ALL, border=5)
        self.chatTitle = wx.StaticText(self.rightPanel, label="")
        topSizer.Add(self.chatTitle, flag=wx.ALL|wx.ALIGN_CENTER_VERTICAL, border=5)
        rightSizer.Add(topSizer, flag=wx.EXPAND)
        rightSizer.Add(wx.StaticText(self.rightPanel, label="Chat History"), flag=wx.ALL, border=5)
        self.messagesText = wx.TextCtrl(self.rightPanel, style=wx.TE_MULTILINE|wx.TE_READONLY|wx.TE_RICH2|wx.TE_DONTWRAP)
        rightSizer.Add(self.messagesText, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        rightSizer.Add(wx.StaticText(self.rightPanel, label="Chat"), flag=wx.ALL, border=5)
        inputSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.messageInput = wx.TextCtrl(self.rightPanel, style=wx.TE_PROCESS_ENTER)
        self.messageInput.Bind(wx.EVT_TEXT_ENTER, self.onSendMessage)
        inputSizer.Add(self.messageInput, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        sendBtn = wx.Button(self.rightPanel, label="&Send")
        sendBtn.Bind(wx.EVT_BUTTON, self.onSendMessage)
        inputSizer.Add(sendBtn, flag=wx.ALL, border=5)
        rightSizer.Add(inputSizer, flag=wx.EXPAND)
        self.rightPanel.SetSizer(rightSizer)
        mainSizer.Add(self.rightPanel, proportion=2, flag=wx.ALL|wx.EXPAND, border=5)
        self.rightPanel.Hide()
        
        panel.SetSizer(mainSizer)
        menuBar = wx.MenuBar()
        fileMenu = wx.Menu()
        connectItem = fileMenu.Append(wx.ID_ANY, "&Connect\tCtrl+C")
        self.Bind(wx.EVT_MENU, lambda e: self.plugin.connect(), connectItem)
        disconnectItem = fileMenu.Append(wx.ID_ANY, "&Disconnect\tCtrl+D")
        self.Bind(wx.EVT_MENU, lambda e: self.plugin.disconnect(), disconnectItem)
        fileMenu.AppendSeparator()
        exitItem = fileMenu.Append(wx.ID_EXIT, "E&xit\tAlt+F4")
        self.Bind(wx.EVT_MENU, self.onClose, exitItem)
        menuBar.Append(fileMenu, "&File")
        friendsMenu = wx.Menu()
        manageFriendsItem = friendsMenu.Append(wx.ID_ANY, "&Manage Friends\tCtrl+F")
        self.Bind(wx.EVT_MENU, self.onManageFriends, manageFriendsItem)
        menuBar.Append(friendsMenu, "F&riends")
        settingsMenu = wx.Menu()
        settingsItem = settingsMenu.Append(wx.ID_ANY, "&Settings\tCtrl+S")
        self.Bind(wx.EVT_MENU, self.onSettings, settingsItem)
        menuBar.Append(settingsMenu, "&Settings")
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
                            ui.message("Only group admin can manage group")
                            return
                    else:
                        ui.message("Not a group chat")
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
                        ui.message("Not a group chat")
                        return
        e.Skip()
    
    def refresh_chats(self):
        self.chatsList.Clear()
        
        # Debug output
        print(f"Refreshing chats. Total chats: {len(self.plugin.chats)}")
        
        if not self.plugin.chats:
            self.chatsList.Append("No chats")
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
                        name = f"{name} (Group - You are admin)"
                    else:
                        name = f"{name} (Group)"
                
                # For private chats, add online/offline status
                if chat_type == 'private' and name != "Unknown":
                    # Check if the other person is online
                    is_online = False
                    for friend in self.plugin.friends:
                        if friend['username'] == name:
                            is_online = (friend.get('status', 'offline') == 'online')
                            break
                    
                    # Add text status indicator (screen reader friendly)
                    status_text = "online" if is_online else "offline"
                    name = f"{name} ({status_text})"
                
                # Add unread count if any
                unread = c.get('unread_count', 0)
                if unread > 0:
                    display = f"{name} - {unread} unread"
                else:
                    display = name
                
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
        for m in messages:
            sender = m.get('sender', 'Unknown')
            text = m.get('message', '')
            timestamp = m.get('timestamp', '')
            is_action = m.get('is_action', False)
            
            # Convert to local time
            date_str = self.format_timestamp(timestamp)
            
            # Format based on whether it's an action or regular message
            if is_action:
                # /me format: username message ; timestamp
                self.messagesText.AppendText(f"{sender} {text} ; {date_str}\n")
            else:
                # Regular format: username; message ; timestamp (space only after message)
                self.messagesText.AppendText(f"{sender}; {text} ; {date_str}\n")
    
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
            
            # Use current PC time instead of server timestamp
            date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Suppress auto-read for ALL messages, we'll manually speak them
            import speech
            speech.setSpeechMode(speech.SpeechMode.off)
            
            # Format and append based on whether it's an action or regular message
            if is_action:
                self.messagesText.AppendText(f"{sender} {text} ; {date_str}\n")
            else:
                self.messagesText.AppendText(f"{sender}; {text} ; {date_str}\n")
            
            # Re-enable speech immediately
            speech.setSpeechMode(speech.SpeechMode.talk)
            
            # Manually speak the message based on settings
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
            ui.message("No friends. Add friends first.")
            return
        
        choices = ["Private Chat", "Group Chat"]
        dlg = wx.SingleChoiceDialog(self, "What type of chat?", "New Chat", choices)
        
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
        dlg = wx.SingleChoiceDialog(self, "Select friend to chat with:", "New Private Chat", [f['username'] for f in self.plugin.friends])
        if dlg.ShowModal() == wx.ID_OK:
            sel = dlg.GetSelection()
            friend = self.plugin.friends[sel]['username']
            self.plugin.create_chat([self.plugin.config['username'], friend], self.on_chat_created, chat_type='private')
        dlg.Destroy()
    
    def create_group_chat(self):
        CreateGroupDialog(self, self.plugin).ShowModal()
    
    def on_chat_created(self, chat_id):
        ui.message("Chat opened")
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
        if sel == wx.NOT_FOUND or not self.plugin.chats: return ui.message("Select chat")
        
        # Get sorted chat list to match display order
        sorted_chats = sorted(
            self.plugin.chats.items(),
            key=lambda x: x[1].get('last_message_time', ''),
            reverse=True
        )
        
        if sel >= len(sorted_chats): return
        chat_id, chat = sorted_chats[sel]
        
        dlg = wx.MessageDialog(self, "Delete this chat?", "Confirm", wx.YES_NO | wx.ICON_QUESTION)
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
        
        menu = wx.Menu()
        
        if chat_type == 'group':
            is_admin = chat.get('admin') == self.plugin.config.get('username')
            
            members_item = menu.Append(wx.ID_ANY, "View Members")
            self.Bind(wx.EVT_MENU, lambda e: self.on_view_members(chat_id), members_item)
            
            if is_admin:
                manage_item = menu.Append(wx.ID_ANY, "Manage Group (Admin)")
                self.Bind(wx.EVT_MENU, lambda e: self.on_manage_group(chat_id), manage_item)
                
                menu.AppendSeparator()
                
                delete_all_item = menu.Append(wx.ID_ANY, "Delete Group for Everyone (Admin)")
                self.Bind(wx.EVT_MENU, lambda e: self.on_delete_group_all(chat_id), delete_all_item)
            
            menu.AppendSeparator()
            delete_local_item = menu.Append(wx.ID_ANY, "Remove from My List")
            self.Bind(wx.EVT_MENU, lambda e: self.onDeleteChat(None), delete_local_item)
        else:
            delete_item = menu.Append(wx.ID_ANY, "Delete Chat")
            self.Bind(wx.EVT_MENU, lambda e: self.onDeleteChat(None), delete_item)
        
        self.PopupMenu(menu)
        menu.Destroy()
    

    
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
        dlg = wx.MessageDialog(self, f"Members of {group_name}:\n\n{members_text}", "Group Members", wx.OK | wx.ICON_INFORMATION)
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
        
        dlg = wx.MessageDialog(self, f"Delete '{group_name}' for EVERYONE?\nThis cannot be undone!", "Delete Group", wx.YES_NO | wx.ICON_WARNING)
        
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.delete_group(chat_id, callback=lambda: self.refresh_chats())
        dlg.Destroy()

    def onBack(self, e):
        """Go back to the chat list - hide the chat panel"""
        self.rightPanel.Hide()
        self.Layout()
        self.chatsList.SetFocus()
    
    def onManageFriends(self, e):
        if not self.plugin.connected: return ui.message("Not connected")
        FriendsDialog(self, self.plugin).ShowModal()
    
    def onSettings(self, e): SettingsDialog(self, self.plugin).ShowModal()
    
    def onAccount(self, e): AccountDialog(self, self.plugin).ShowModal()
    
    def onClose(self, e):
        self.Hide()
        e.Veto()


class CreateGroupDialog(wx.Dialog):
    def __init__(self, parent, plugin):
        super().__init__(parent, title="Create Group Chat", size=(500, 400))
        self.plugin = plugin
        self.Bind(wx.EVT_CHAR_HOOK, self.onKeyPress)
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        sizer.Add(wx.StaticText(self, label="Group Name:"), flag=wx.ALL, border=5)
        self.nameText = wx.TextCtrl(self)
        sizer.Add(self.nameText, flag=wx.ALL|wx.EXPAND, border=5)
        
        sizer.Add(wx.StaticText(self, label="Select Members (Space to toggle):"), flag=wx.ALL, border=5)
        
        self.membersList = wx.CheckListBox(self, choices=[f['username'] for f in plugin.friends])
        self.membersList.Bind(wx.EVT_CHECKLISTBOX, self.onMemberToggle)
        self.membersList.Bind(wx.EVT_LISTBOX, self.onMemberSelect)
        self.membersList.Bind(wx.EVT_CHAR_HOOK, self.onListKeyPress)
        sizer.Add(self.membersList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        selectAllBtn = wx.Button(self, label="Select All")
        selectAllBtn.Bind(wx.EVT_BUTTON, self.onSelectAll)
        btnSizer.Add(selectAllBtn, flag=wx.ALL, border=5)
        
        deselectAllBtn = wx.Button(self, label="Deselect All")
        deselectAllBtn.Bind(wx.EVT_BUTTON, self.onDeselectAll)
        btnSizer.Add(deselectAllBtn, flag=wx.ALL, border=5)
        sizer.Add(btnSizer, flag=wx.ALIGN_CENTER)
        
        btnSizer2 = wx.BoxSizer(wx.HORIZONTAL)
        createBtn = wx.Button(self, label="Create Group")
        createBtn.Bind(wx.EVT_BUTTON, self.onCreate)
        btnSizer2.Add(createBtn, flag=wx.ALL, border=5)
        
        cancelBtn = wx.Button(self, label="Cancel")
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
        ui.message("All members deselected")
    
    def onCreate(self, e):
        group_name = self.nameText.GetValue().strip()
        
        if not group_name:
            ui.message("Enter a group name")
            return
        
        selected_members = []
        for i in range(self.membersList.GetCount()):
            if self.membersList.IsChecked(i):
                selected_members.append(self.plugin.friends[i]['username'])
        
        if len(selected_members) < 1:
            ui.message("Select at least one member")
            return
        
        participants = [self.plugin.config.get('username')] + selected_members
        
        self.plugin.create_chat(participants, callback=lambda chat_id: self.on_group_created(chat_id), chat_type='group', group_name=group_name)
        
        self.Close()
    
    def on_group_created(self, chat_id):
        ui.message("Group created")
        if self.GetParent():
            self.GetParent().refresh_chats()


class ManageGroupDialog(wx.Dialog):
    """Comprehensive group management dialog for admins"""
    
    def __init__(self, parent, plugin, chat_id):
        self.plugin = plugin
        self.chat_id = chat_id
        self.chat = plugin.chats.get(chat_id, {})
        
        group_name = self.chat.get('name', 'Group')
        super().__init__(parent, title=f"Manage Group: {group_name}", size=(600, 500))
        
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        
        # Group name section
        nameSizer = wx.BoxSizer(wx.HORIZONTAL)
        nameSizer.Add(wx.StaticText(self, label="Group Name:"), flag=wx.ALL|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.nameText = wx.TextCtrl(self, value=group_name)
        nameSizer.Add(self.nameText, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        renameBtn = wx.Button(self, label="Rename")
        renameBtn.Bind(wx.EVT_BUTTON, self.onRename)
        nameSizer.Add(renameBtn, flag=wx.ALL, border=5)
        mainSizer.Add(nameSizer, flag=wx.EXPAND|wx.ALL, border=5)
        
        mainSizer.Add(wx.StaticLine(self), flag=wx.EXPAND|wx.ALL, border=5)
        
        # Members section
        mainSizer.Add(wx.StaticText(self, label="Group Members (Arrow keys to navigate):"), flag=wx.ALL, border=5)
        
        # Member list
        self.membersList = wx.ListBox(self)
        self.membersList.Bind(wx.EVT_LISTBOX, self.onMemberSelect)
        self.membersList.Bind(wx.EVT_CHAR_HOOK, self.onMembersKeyPress)
        mainSizer.Add(self.membersList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Member action buttons
        memberBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.addBtn = wx.Button(self, label="Add Member")
        self.addBtn.Bind(wx.EVT_BUTTON, self.onAddMember)
        memberBtnSizer.Add(self.addBtn, flag=wx.ALL, border=5)
        
        self.removeBtn = wx.Button(self, label="Remove Selected Member")
        self.removeBtn.Bind(wx.EVT_BUTTON, self.onRemoveMember)
        memberBtnSizer.Add(self.removeBtn, flag=wx.ALL, border=5)
        
        self.promoteBtn = wx.Button(self, label="Make Admin (Transfer)")
        self.promoteBtn.Bind(wx.EVT_BUTTON, self.onTransferAdmin)
        memberBtnSizer.Add(self.promoteBtn, flag=wx.ALL, border=5)
        
        mainSizer.Add(memberBtnSizer, flag=wx.ALIGN_CENTER)
        
        mainSizer.Add(wx.StaticLine(self), flag=wx.EXPAND|wx.ALL, border=5)
        
        # Bottom buttons
        bottomBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        
        refreshBtn = wx.Button(self, label="Refresh")
        refreshBtn.Bind(wx.EVT_BUTTON, lambda e: self.refreshMembers())
        bottomBtnSizer.Add(refreshBtn, flag=wx.ALL, border=5)
        
        closeBtn = wx.Button(self, label="Close")
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
        ui.message(f"{len(participants)} members in group")
    
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
            ui.message("Enter a group name")
            self.nameText.SetFocus()
            return
        
        if new_name == current_name:
            ui.message("Name unchanged")
            return
        
        # Confirm rename
        dlg = wx.MessageDialog(
            self,
            f"Rename group to '{new_name}'?",
            "Confirm Rename",
            wx.YES_NO | wx.ICON_QUESTION
        )
        
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.rename_group(self.chat_id, new_name, callback=lambda: self.onRenameComplete(new_name))
        dlg.Destroy()
    
    def onRenameComplete(self, new_name):
        """Called after successful rename"""
        self.SetTitle(f"Manage Group: {new_name}")
        ui.message(f"Renamed to {new_name}")
        if self.GetParent():
            self.GetParent().refresh_chats()
    
    def onAddMember(self, e):
        """Add a member to the group"""
        current_members = self.chat.get('participants', [])
        available = [f['username'] for f in self.plugin.friends if f['username'] not in current_members]
        
        if not available:
            ui.message("No friends available to add")
            return
        
        dlg = wx.SingleChoiceDialog(
            self,
            "Select friend to add (Enter to confirm):",
            "Add Group Member",
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
        ui.message(f"Added {username}")
        # Reload chat data and refresh
        wx.CallLater(500, self.plugin.load_chats)
        wx.CallLater(700, self.refreshMembers)
    
    def onRemoveMember(self, e):
        """Remove selected member from group"""
        sel = self.membersList.GetSelection()
        if sel == wx.NOT_FOUND:
            ui.message("Select a member first")
            return
        
        participants = self.chat.get('participants', [])
        admin = self.chat.get('admin', '')
        
        if sel >= len(participants):
            return
        
        username = participants[sel]
        
        # Can't remove admin
        if username == admin:
            ui.message("Cannot remove admin")
            return
        
        # Can't remove yourself
        if username == self.plugin.config.get('username'):
            ui.message("Cannot remove yourself")
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
            ui.message("Select a member first")
            return
        
        participants = self.chat.get('participants', [])
        admin = self.chat.get('admin', '')
        
        if sel >= len(participants):
            return
        
        username = participants[sel]
        
        # Can't transfer to yourself
        if username == self.plugin.config.get('username'):
            ui.message("You are already admin")
            return
        
        # Confirm transfer
        dlg = wx.MessageDialog(
            self,
            f"Transfer admin rights to {username}?\n\nYou will no longer be admin!",
            "Confirm Transfer",
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
        super().__init__(parent, title="Friends", size=(600, 500))
        self.plugin = plugin
        self.pending_requests = []
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self)
        friendsPanel = wx.Panel(self.notebook)
        friendsSizer = wx.BoxSizer(wx.VERTICAL)
        friendsSizer.Add(wx.StaticText(friendsPanel, label="My Friends"), flag=wx.ALL, border=5)
        self.friendsList = wx.ListCtrl(friendsPanel, style=wx.LC_REPORT)
        self.friendsList.InsertColumn(0, "Username", width=250)
        self.friendsList.InsertColumn(1, "Status", width=100)
        friendsSizer.Add(self.friendsList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        friendsBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        deleteBtn = wx.Button(friendsPanel, label="&Delete Friend")
        deleteBtn.Bind(wx.EVT_BUTTON, self.onDeleteFriend)
        friendsBtnSizer.Add(deleteBtn, flag=wx.ALL, border=5)
        friendsSizer.Add(friendsBtnSizer, flag=wx.ALIGN_CENTER)
        friendsPanel.SetSizer(friendsSizer)
        requestsPanel = wx.Panel(self.notebook)
        requestsSizer = wx.BoxSizer(wx.VERTICAL)
        requestsSizer.Add(wx.StaticText(requestsPanel, label="Friend Requests"), flag=wx.ALL, border=5)
        self.requestsList = wx.ListBox(requestsPanel)
        requestsSizer.Add(self.requestsList, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        reqBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in [("&Accept", self.onAccept), ("&Reject", self.onReject)]:
            btn = wx.Button(requestsPanel, label=label)
            btn.Bind(wx.EVT_BUTTON, handler)
            reqBtnSizer.Add(btn, flag=wx.ALL, border=5)
        requestsSizer.Add(reqBtnSizer, flag=wx.ALIGN_CENTER)
        requestsPanel.SetSizer(requestsSizer)
        self.notebook.AddPage(friendsPanel, "My Friends")
        self.notebook.AddPage(requestsPanel, "Requests")
        mainSizer.Add(self.notebook, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        # REMOVED THE CLOSE BUTTON - Only Add Friend and Refresh buttons remain
        for label, handler in [("&Add Friend", self.onAdd), ("&Refresh", self.onRefresh)]:
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
                self.friendsList.SetItem(idx, 1, f.get('status', 'offline'))
    
    def displayRequests(self, incoming, outgoing):
        self.requestsList.Clear()
        self.pending_requests = incoming
        if not incoming and not outgoing: self.requestsList.Append("No requests")
        else:
            if incoming:
                self.requestsList.Append("=== INCOMING ===")
                for u in incoming: self.requestsList.Append(f"{u}")
            if outgoing:
                if incoming: self.requestsList.Append("")
                self.requestsList.Append("=== OUTGOING ===")
                for u in outgoing: self.requestsList.Append(f"{u} (waiting)")
        self.notebook.SetPageText(1, f"Requests ({len(incoming)})" if incoming else "Requests")
    
    def onAccept(self, e):
        sel = self.requestsList.GetSelection()
        if sel == wx.NOT_FOUND: return ui.message("Select request")
        txt = self.requestsList.GetString(sel).strip()
        if "(Outgoing)" in txt or "No" in txt or not txt: 
            return ui.message("Select an incoming request")
        
        # Extract username (remove the "(Incoming)" part)
        if "(Incoming)" in txt:
            txt = txt.replace(" (Incoming)", "")
        username = txt.split()[0]
        if username not in self.pending_requests: return ui.message("Invalid")
        ui.message(f"Accepting {username}...")
        def accept():
            try:
                resp = requests.post(f'{self.plugin.config["server_url"]}/api/friends/accept', headers={'Authorization': f'Bearer {self.plugin.token}'}, json={'username': username}, timeout=10)
                if resp.status_code == 200:
                    wx.CallAfter(lambda: (ui.message(f"Accepted!"), self.plugin.playSound('user_online'), self.loadFriendsData(), self.plugin.load_friends()))
            except: wx.CallAfter(lambda: ui.message("Error"))
        threading.Thread(target=accept, daemon=True).start()
    
    def onReject(self, e): ui.message("Coming soon")
    
    def onRefresh(self, e):
        self.loadFriendsData()
        ui.message("Refreshing...")
    
    def onDeleteFriend(self, e):
        sel = self.friendsList.GetFirstSelected()
        if sel == -1: return ui.message("Select friend")
        username = self.friendsList.GetItemText(sel, 0)
        if not username or username == "No friends": return
        dlg = wx.MessageDialog(self, f"Delete {username}?", "Confirm", wx.YES_NO | wx.ICON_QUESTION)
        if dlg.ShowModal() == wx.ID_YES:
            self.plugin.delete_friend(username)
            wx.CallLater(1000, self.loadFriendsData)
        dlg.Destroy()
    
    def onAdd(self, e):
        dlg = wx.TextEntryDialog(self, "Friend's username:", "Add Friend")
        if dlg.ShowModal() == wx.ID_OK:
            username = dlg.GetValue().strip()
            if username:
                def add():
                    try:
                        resp = requests.post(f'{self.plugin.config["server_url"]}/api/friends/add', headers={'Authorization': f'Bearer {self.plugin.token}'}, json={'username': username}, timeout=10)
                        if resp.status_code == 200: wx.CallAfter(lambda: (ui.message("Request sent!"), self.loadFriendsData()))
                        else: wx.CallAfter(lambda: ui.message("Error"))
                    except: wx.CallAfter(lambda: ui.message("Connection error"))
                threading.Thread(target=add, daemon=True).start()
        dlg.Destroy()

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, plugin):
        super().__init__(parent, title="Settings", size=(600, 600))
        self.plugin = plugin
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        
        # Create notebook for tabs
        self.notebook = wx.Notebook(self)
        
        # General Tab
        generalPanel = wx.Panel(self.notebook)
        generalSizer = wx.BoxSizer(wx.VERTICAL)
        
        # Local message saving
        generalSizer.Add(wx.StaticText(generalPanel, label="Message History:"), flag=wx.ALL, border=5)
        self.saveLocalCheck = wx.CheckBox(generalPanel, label="Save chat messages locally")
        self.saveLocalCheck.SetValue(plugin.config.get("save_messages_locally", True))
        generalSizer.Add(self.saveLocalCheck, flag=wx.ALL, border=5)
        
        # Messages folder selection
        folderSizer = wx.BoxSizer(wx.HORIZONTAL)
        generalSizer.Add(wx.StaticText(generalPanel, label="Messages folder:"), flag=wx.ALL, border=5)
        self.messagesFolderText = wx.TextCtrl(generalPanel, value=plugin.config.get("messages_folder", os.path.join(os.path.expanduser("~"), "NVDA Chat Messages")))
        folderSizer.Add(self.messagesFolderText, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        browseBtn = wx.Button(generalPanel, label="&Browse...")
        browseBtn.Bind(wx.EVT_BUTTON, self.onBrowseFolder)
        folderSizer.Add(browseBtn, flag=wx.ALL, border=5)
        generalSizer.Add(folderSizer, flag=wx.EXPAND)
        
        # Logging section
        generalSizer.Add(wx.StaticLine(generalPanel), flag=wx.ALL|wx.EXPAND, border=10)
        generalSizer.Add(wx.StaticText(generalPanel, label="Logging:"), flag=wx.ALL, border=5)
        logBtn = wx.Button(generalPanel, label="&View NVDA Log")
        logBtn.Bind(wx.EVT_BUTTON, self.onViewLog)
        generalSizer.Add(logBtn, flag=wx.ALL, border=5)
        
        # Updates section
        generalSizer.Add(wx.StaticLine(generalPanel), flag=wx.ALL|wx.EXPAND, border=10)
        generalSizer.Add(wx.StaticText(generalPanel, label="Updates:"), flag=wx.ALL, border=5)
        
        self.checkUpdatesStartup = wx.CheckBox(generalPanel, label="Check for updates on startup")
        self.checkUpdatesStartup.SetValue(plugin.config.get('check_updates_on_startup', True))
        generalSizer.Add(self.checkUpdatesStartup, flag=wx.ALL, border=5)
        
        versionText = wx.StaticText(generalPanel, label=f"Current version: {ADDON_VERSION}")
        generalSizer.Add(versionText, flag=wx.ALL, border=5)
        
        updateBtn = wx.Button(generalPanel, label="&Check for Updates Now")
        updateBtn.Bind(wx.EVT_BUTTON, lambda e: plugin.check_for_updates(silent=False))
        generalSizer.Add(updateBtn, flag=wx.ALL, border=5)
        
        generalPanel.SetSizer(generalSizer)
        
        # Sounds Tab
        soundsPanel = wx.Panel(self.notebook)
        soundsSizer = wx.BoxSizer(wx.VERTICAL)
        self.soundCheck = wx.CheckBox(soundsPanel, label="Enable all sounds")
        self.soundCheck.SetValue(plugin.config.get("sound_enabled", True))
        soundsSizer.Add(self.soundCheck, flag=wx.ALL, border=5)
        soundsSizer.Add(wx.StaticLine(soundsPanel), flag=wx.ALL|wx.EXPAND, border=5)
        soundsSizer.Add(wx.StaticText(soundsPanel, label="Individual Sound Settings:"), flag=wx.ALL, border=5)
        
        # Individual sound checkboxes
        self.sound_checks = {}
        sound_labels = {
            "sound_message_received": "Message received",
            "sound_message_sent": "Message sent",
            "sound_user_online": "User comes online",
            "sound_user_offline": "User goes offline",
            "sound_friend_request": "Friend request",
            "sound_error": "Error",
            "sound_connected": "Connected",
            "sound_disconnected": "Disconnected"
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
        notifSizer.Add(wx.StaticText(notifPanel, label="Speech Notifications:"), flag=wx.ALL, border=5)
        
        self.readMessagesCheck = wx.CheckBox(notifPanel, label="Read messages aloud when in chat window")
        self.readMessagesCheck.SetValue(plugin.config.get("read_messages_aloud", True))
        notifSizer.Add(self.readMessagesCheck, flag=wx.ALL, border=5)
        notifSizer.Add(wx.StaticLine(notifPanel), flag=wx.ALL|wx.EXPAND, border=5)
        
        # Individual speak checkboxes
        self.speak_checks = {}
        speak_labels = {
            "speak_message_received": "Speak when message received",
            "speak_message_sent": "Speak when message sent",
            "speak_user_online": "Speak when user comes online",
            "speak_user_offline": "Speak when user goes offline",
            "speak_friend_request": "Speak friend requests"
        }
        for key, label in speak_labels.items():
            check = wx.CheckBox(notifPanel, label=label)
            check.SetValue(plugin.config.get(key, True))
            self.speak_checks[key] = check
            notifSizer.Add(check, flag=wx.ALL, border=5)
        notifPanel.SetSizer(notifSizer)
        
        # Add tabs to notebook
        self.notebook.AddPage(generalPanel, "General")
        self.notebook.AddPage(soundsPanel, "Sounds")
        self.notebook.AddPage(notifPanel, "Notifications")
        mainSizer.Add(self.notebook, proportion=1, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Buttons
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        saveBtn = wx.Button(self, label="&Save")
        saveBtn.Bind(wx.EVT_BUTTON, self.onSave)
        cancelBtn = wx.Button(self, label="&Cancel")
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
            ui.message("Could not open log viewer")
    
    def onSave(self, e):
        # Save general settings (no account settings)
        self.plugin.config.update({
            "sound_enabled": self.soundCheck.GetValue(),
            "read_messages_aloud": self.readMessagesCheck.GetValue(),
            "save_messages_locally": self.saveLocalCheck.GetValue(),
            "messages_folder": self.messagesFolderText.GetValue()
        })
        
        # Save individual sound settings
        for key, check in self.sound_checks.items():
            self.plugin.config[key] = check.GetValue()
        
        # Save individual speak settings
        for key, check in self.speak_checks.items():
            self.plugin.config[key] = check.GetValue()
        
        self.plugin.saveConfig()
        ui.message("Saved!")
        self.Close()

class AccountDialog(wx.Dialog):
    def __init__(self, parent, plugin):
        super().__init__(parent, title="Account Settings", size=(500, 450))
        self.plugin = plugin
        self.Bind(wx.EVT_CHAR_HOOK, lambda e: self.Close() if e.GetKeyCode() == wx.WXK_ESCAPE else e.Skip())
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Server settings
        sizer.Add(wx.StaticText(self, label="Server URL:"), flag=wx.ALL, border=5)
        self.serverText = wx.TextCtrl(self, value=plugin.config["server_url"])
        sizer.Add(self.serverText, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Account information
        sizer.Add(wx.StaticLine(self), flag=wx.ALL|wx.EXPAND, border=10)
        sizer.Add(wx.StaticText(self, label="Account Information:"), flag=wx.ALL, border=5)
        
        sizer.Add(wx.StaticText(self, label="Username:"), flag=wx.ALL, border=5)
        self.userText = wx.TextCtrl(self, value=plugin.config["username"])
        sizer.Add(self.userText, flag=wx.ALL|wx.EXPAND, border=5)
        
        sizer.Add(wx.StaticText(self, label="Password:"), flag=wx.ALL, border=5)
        self.passText = wx.TextCtrl(self, value=plugin.config["password"], style=wx.TE_PASSWORD)
        sizer.Add(self.passText, flag=wx.ALL|wx.EXPAND, border=5)
        
        sizer.Add(wx.StaticText(self, label="Email (optional):"), flag=wx.ALL, border=5)
        self.emailText = wx.TextCtrl(self, value=plugin.config.get("email", ""))
        sizer.Add(self.emailText, flag=wx.ALL|wx.EXPAND, border=5)
        
        # Auto-connect
        self.autoCheck = wx.CheckBox(self, label="Auto-connect on startup")
        self.autoCheck.SetValue(plugin.config.get("auto_connect", False))
        sizer.Add(self.autoCheck, flag=wx.ALL, border=5)
        
        # Buttons
        sizer.Add(wx.StaticLine(self), flag=wx.ALL|wx.EXPAND, border=10)
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        
        registerBtn = wx.Button(self, label="&Register New Account")
        registerBtn.Bind(wx.EVT_BUTTON, self.onRegister)
        btnSizer.Add(registerBtn, flag=wx.ALL, border=5)
        
        saveBtn = wx.Button(self, label="&Save")
        saveBtn.Bind(wx.EVT_BUTTON, self.onSave)
        btnSizer.Add(saveBtn, flag=wx.ALL, border=5)
        
        cancelBtn = wx.Button(self, label="&Cancel")
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
            return ui.message("Enter username and password")
        if len(password) < 6:
            return ui.message("Password must be 6+ characters")
        
        ui.message("Creating account...")
        
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
                    wx.CallAfter(lambda: ui.message("Username taken"))
                else:
                    wx.CallAfter(lambda: ui.message("Registration failed"))
            except:
                wx.CallAfter(lambda: ui.message("Cannot reach server"))
        
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
        ui.message("Account settings saved!")
        self.Close()
