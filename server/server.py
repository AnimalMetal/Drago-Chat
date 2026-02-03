#!/usr/bin/env python3
"""
NVDA Chat Server - Restructured with Individual User Folders
Version 2.0 - Privacy-focused, organized data storage
"""

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from flask_cors import CORS
import os
import json
import bcrypt
import jwt
import time
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
CORS(app)
socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode='eventlet',
    ping_interval=25,
    ping_timeout=60,
    logger=False,
    engineio_logger=False
)

# Paths - New Structure
DATA_PATH = '/home/metal/nvda-chat-server/data'
USERS_DIR = os.path.join(DATA_PATH, 'users')
USERS_INDEX_FILE = os.path.join(DATA_PATH, 'users_index.json')
CHATS_FILE = os.path.join(DATA_PATH, 'chats.json')

# Create directories
os.makedirs(DATA_PATH, exist_ok=True)
os.makedirs(USERS_DIR, exist_ok=True)

# In-memory storage for online users
online_users = {}  # {username: sid}
user_sessions = {}  # {sid: username}

# Helper Functions
def get_user_dir(username):
    """Get or create user-specific directory"""
    user_dir = os.path.join(USERS_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def get_user_file(username, filename):
    """Get path to user-specific file"""
    return os.path.join(get_user_dir(username), filename)

def load_json(filepath, default=None):
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return default if default is not None else {}

def save_json(filepath, data):
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving {filepath}: {e}")
        return False

def load_user_data(username):
    """Load user's profile data"""
    user_file = get_user_file(username, 'profile.json')
    return load_json(user_file, {})

def save_user_data(username, data):
    """Save user's profile data"""
    user_file = get_user_file(username, 'profile.json')
    return save_json(user_file, data)

def load_user_friends(username):
    """Load user's friends list"""
    friends_file = get_user_file(username, 'friends.json')
    return load_json(friends_file, {})

def save_user_friends(username, friends_data):
    """Save user's friends list"""
    friends_file = get_user_file(username, 'friends.json')
    return save_json(friends_file, friends_data)

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_token(username):
    payload = {
        'username': username,
        'exp': datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload['username']
    except:
        return None

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Token required'}), 401
        if token.startswith('Bearer '):
            token = token[7:]
        username = verify_token(token)
        if not username:
            return jsonify({'error': 'Invalid token'}), 401
        return f(username, *args, **kwargs)
    return decorated

# API Endpoints

@app.route('/')
def index():
    return jsonify({
        'name': 'NVDA Chat Server',
        'version': '2.0.0',
        'status': 'running',
        'online_users': len(online_users),
        'structure': 'Individual user folders'
    })

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip()
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    # Check users index
    users_index = load_json(USERS_INDEX_FILE, {})
    
    if username in users_index:
        return jsonify({'error': 'Username already exists'}), 409
    
    # Add to users index (only username and password hash)
    users_index[username] = {
        'password': hash_password(password),
        'created_at': datetime.now().isoformat()
    }
    save_json(USERS_INDEX_FILE, users_index)
    
    # Create user directory and profile
    user_profile = {
        'username': username,
        'email': email,
        'display_name': username,
        'created_at': datetime.now().isoformat(),
        'status': 'offline'
    }
    save_user_data(username, user_profile)
    
    # Initialize empty friends list
    save_user_friends(username, {})
    
    token = create_token(username)
    
    return jsonify({
        'success': True,
        'token': token,
        'username': username
    })

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    users_index = load_json(USERS_INDEX_FILE, {})
    
    if username not in users_index:
        return jsonify({'error': 'Invalid credentials'}), 401
    
    user = users_index[username]
    
    if not check_password(password, user['password']):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    token = create_token(username)
    
    # Load user profile
    profile = load_user_data(username)
    
    return jsonify({
        'success': True,
        'token': token,
        'username': username,
        'display_name': profile.get('display_name', username)
    })

@app.route('/api/friends', methods=['GET'])
@token_required
def get_friends(username):
    user_friends = load_user_friends(username)
    
    friends_list = []
    pending_outgoing = []
    pending_incoming = []
    
    for friend_username, status in user_friends.items():
        if status == 'accepted':
            is_online = friend_username in online_users
            friends_list.append({
                'username': friend_username,
                'status': 'online' if is_online else 'offline'
            })
        elif status == 'pending':
            pending_outgoing.append(friend_username)
        elif status == 'request':
            pending_incoming.append(friend_username)
    
    return jsonify({
        'success': True,
        'friends': friends_list,
        'pending_outgoing': pending_outgoing,
        'pending_incoming': pending_incoming
    })

@app.route('/api/friends/delete', methods=['POST'])
@token_required
def delete_friend(username):
    data = request.json
    friend_username = data.get('username', '').strip()
    
    if not friend_username:
        return jsonify({'error': 'Friend username required'}), 400
    
    # Remove from both users' friends lists
    user_friends = load_user_friends(username)
    if friend_username in user_friends:
        del user_friends[friend_username]
        save_user_friends(username, user_friends)
    
    friend_friends = load_user_friends(friend_username)
    if username in friend_friends:
        del friend_friends[username]
        save_user_friends(friend_username, friend_friends)
    
    return jsonify({'success': True, 'message': 'Friend deleted'})

@app.route('/api/friends/add', methods=['POST'])
@token_required
def add_friend(username):
    data = request.json
    friend_username = data.get('username', '').strip()
    
    if not friend_username:
        return jsonify({'error': 'Friend username required'}), 400
    
    if friend_username == username:
        return jsonify({'error': 'Cannot add yourself'}), 400
    
    users_index = load_json(USERS_INDEX_FILE, {})
    if friend_username not in users_index:
        return jsonify({'error': 'User not found'}), 404
    
    user_friends = load_user_friends(username)
    
    if friend_username in user_friends:
        return jsonify({'error': 'Friend request already sent or already friends'}), 409
    
    # Add pending request
    user_friends[friend_username] = 'pending'
    save_user_friends(username, user_friends)
    
    # Add incoming request to friend
    friend_friends = load_user_friends(friend_username)
    friend_friends[username] = 'request'
    save_user_friends(friend_username, friend_friends)
    
    # Notify if online
    if friend_username in online_users:
        socketio.emit('friend_request', {
            'from': username,
            'timestamp': datetime.now().isoformat()
        }, room=online_users[friend_username])
    
    return jsonify({'success': True, 'message': 'Friend request sent'})

@app.route('/api/friends/accept', methods=['POST'])
@token_required
def accept_friend(username):
    data = request.json
    friend_username = data.get('username', '').strip()
    
    user_friends = load_user_friends(username)
    
    if friend_username not in user_friends or user_friends[friend_username] != 'request':
        return jsonify({'error': 'Friend request not found'}), 404
    
    # Accept the request
    user_friends[friend_username] = 'accepted'
    save_user_friends(username, user_friends)
    
    friend_friends = load_user_friends(friend_username)
    friend_friends[username] = 'accepted'
    save_user_friends(friend_username, friend_friends)
    
    # Notify both users
    if friend_username in online_users:
        socketio.emit('friend_accepted', {
            'username': username
        }, room=online_users[friend_username])
    
    return jsonify({'success': True, 'message': 'Friend request accepted'})

@app.route('/api/chats', methods=['GET'])
@token_required
def get_chats(username):
    chats_data = load_json(CHATS_FILE, {})
    
    user_chats = []
    needs_save = False
    for chat_id, chat_info in chats_data.items():
        if username in chat_info['participants']:
            # Migration: Fix old groups without admin field
            if chat_info['type'] == 'group' and 'admin' not in chat_info:
                # Set creator as admin, or first participant if creator unknown
                admin = chat_info.get('created_by', chat_info['participants'][0])
                chat_info['admin'] = admin
                needs_save = True
            
            user_chats.append({
                'chat_id': chat_id,
                'type': chat_info['type'],
                'name': chat_info.get('name', ''),
                'participants': chat_info['participants'],
                'admin': chat_info.get('admin'),  # Include admin for groups
                'created_by': chat_info.get('created_by'),  # Include creator
                'last_message': None,  # Messages stored locally
                'unread_count': 0  # Tracked locally
            })
    
    # Save if we migrated any old groups
    if needs_save:
        save_json(CHATS_FILE, chats_data)
    
    return jsonify({
        'success': True,
        'chats': user_chats
    })

@app.route('/api/chats/create', methods=['POST'])
@token_required
def create_chat(username):
    data = request.json
    participants = data.get('participants', [])
    chat_type = data.get('type', 'private')
    chat_name = data.get('name', '')
    
    if username not in participants:
        participants.append(username)
    
    if chat_type == 'private' and len(participants) != 2:
        return jsonify({'error': 'Private chat must have exactly 2 participants'}), 400
    
    if chat_type == 'group' and len(participants) < 2:
        return jsonify({'error': 'Group must have at least 2 participants'}), 400
    
    if chat_type == 'group' and not chat_name:
        return jsonify({'error': 'Group name required'}), 400
    
    chats_data = load_json(CHATS_FILE, {})
    
    # Check if private chat already exists
    if chat_type == 'private':
        for chat_id, chat_info in chats_data.items():
            if chat_info['type'] == 'private' and set(chat_info['participants']) == set(participants):
                return jsonify({
                    'success': True,
                    'chat_id': chat_id,
                    'existing': True
                })
    
    # Create new chat
    chat_id = f"chat_{int(time.time() * 1000)}"
    chats_data[chat_id] = {
        'type': chat_type,
        'name': chat_name if chat_type == 'group' else '',
        'participants': participants,
        'created_at': datetime.now().isoformat(),
        'created_by': username,
        'admin': username if chat_type == 'group' else None
    }
    
    save_json(CHATS_FILE, chats_data)
    
    # For groups, send automatic welcome message to all members
    if chat_type == 'group':
        # Create welcome message
        welcome_msg = {
            'sender': 'System',
            'message': f'{username} created the group "{chat_name}" and added you',
            'timestamp': datetime.now().isoformat(),
            'is_action': False
        }
        
        # Send message to all participants (including creator)
        for participant in participants:
            if participant in online_users:
                socketio.emit('new_message', {
                    'chat_id': chat_id,
                    'message': welcome_msg
                }, room=online_users[participant])
    
    return jsonify({
        'success': True,
        'chat_id': chat_id
    })

@app.route('/api/chats/delete/<chat_id>', methods=['DELETE'])
@token_required
def delete_chat(username, chat_id):
    chats_data = load_json(CHATS_FILE, {})
    
    if chat_id not in chats_data:
        return jsonify({'error': 'Chat not found'}), 404
    
    if username not in chats_data[chat_id]['participants']:
        return jsonify({'error': 'Not authorized'}), 403
    
    # Delete chat (messages are local, so just remove chat reference)
    del chats_data[chat_id]
    save_json(CHATS_FILE, chats_data)
    
    return jsonify({'success': True, 'message': 'Chat deleted'})


# Group Management Endpoints

@app.route('/api/chats/group/add-member', methods=['POST'])
@token_required
def add_group_member(username):
    data = request.json
    chat_id = data.get('chat_id')
    new_member = data.get('username')
    
    chats_data = load_json(CHATS_FILE, {})
    
    if chat_id not in chats_data:
        return jsonify({'error': 'Chat not found'}), 404
    
    chat = chats_data[chat_id]
    
    if chat.get('admin') != username:
        return jsonify({'error': 'Not authorized'}), 403
    
    if new_member not in chat['participants']:
        chat['participants'].append(new_member)
        save_json(CHATS_FILE, chats_data)
        
        for participant in chat['participants']:
            if participant in online_users:
                socketio.emit('group_member_added', {
                    'chat_id': chat_id,
                    'username': new_member,
                    'added_by': username,
                    'group_name': chat.get('name', 'Group')
                }, room=online_users[participant])
    
    return jsonify({'success': True})

@app.route('/api/chats/group/remove-member', methods=['POST'])
@token_required
def remove_group_member(username):
    data = request.json
    chat_id = data.get('chat_id')
    remove_member = data.get('username')
    
    chats_data = load_json(CHATS_FILE, {})
    
    if chat_id not in chats_data:
        return jsonify({'error': 'Chat not found'}), 404
    
    chat = chats_data[chat_id]
    
    if chat.get('admin') != username:
        return jsonify({'error': 'Not authorized'}), 403
    
    if remove_member == chat.get('admin'):
        return jsonify({'error': 'Cannot remove admin'}), 400
    
    if remove_member in chat['participants']:
        chat['participants'].remove(remove_member)
        save_json(CHATS_FILE, chats_data)
        
        for participant in chat['participants']:
            if participant in online_users:
                socketio.emit('group_member_removed', {
                    'chat_id': chat_id,
                    'username': remove_member,
                    'removed_by': username,
                    'group_name': chat.get('name', 'Group')
                }, room=online_users[participant])
        
        if remove_member in online_users:
            socketio.emit('group_member_removed', {
                'chat_id': chat_id,
                'username': remove_member,
                'removed_by': username,
                'group_name': chat.get('name', 'Group')
            }, room=online_users[remove_member])
    
    return jsonify({'success': True})

@app.route('/api/chats/group/rename', methods=['POST'])
@token_required
def rename_group(username):
    data = request.json
    chat_id = data.get('chat_id')
    new_name = data.get('new_name', '').strip()
    
    if not new_name:
        return jsonify({'error': 'Name required'}), 400
    
    chats_data = load_json(CHATS_FILE, {})
    
    if chat_id not in chats_data:
        return jsonify({'error': 'Chat not found'}), 404
    
    chat = chats_data[chat_id]
    
    if chat.get('admin') != username:
        return jsonify({'error': 'Not authorized'}), 403
    
    old_name = chat.get('name', '')
    chat['name'] = new_name
    save_json(CHATS_FILE, chats_data)
    
    for participant in chat['participants']:
        if participant in online_users:
            socketio.emit('group_renamed', {
                'chat_id': chat_id,
                'old_name': old_name,
                'new_name': new_name,
                'renamed_by': username
            }, room=online_users[participant])
    
    return jsonify({'success': True})



@app.route('/api/chats/group/transfer-admin', methods=['POST'])
@token_required
def transfer_admin(username):
    data = request.json
    chat_id = data.get('chat_id')
    new_admin = data.get('new_admin')
    
    chats_data = load_json(CHATS_FILE, {})
    
    if chat_id not in chats_data:
        return jsonify({'error': 'Chat not found'}), 404
    
    chat = chats_data[chat_id]
    
    # Only current admin can transfer
    if chat.get('admin') != username:
        return jsonify({'error': 'Not authorized'}), 403
    
    # New admin must be in group
    if new_admin not in chat['participants']:
        return jsonify({'error': 'User not in group'}), 400
    
    # Transfer admin
    old_admin = chat.get('admin')
    chat['admin'] = new_admin
    save_json(CHATS_FILE, chats_data)
    
    # Send notification message to group
    transfer_msg = {
        'sender': 'System',
        'message': f'{old_admin} transferred admin rights to {new_admin}',
        'timestamp': datetime.now().isoformat(),
        'is_action': False
    }
    
    # Notify all participants
    for participant in chat['participants']:
        if participant in online_users:
            socketio.emit('new_message', {
                'chat_id': chat_id,
                'message': transfer_msg
            }, room=online_users[participant])
            # Also send event for immediate admin status update
            socketio.emit('admin_transferred', {
                'chat_id': chat_id,
                'old_admin': old_admin,
                'new_admin': new_admin
            }, room=online_users[participant])
    
    return jsonify({'success': True})

@app.route('/api/chats/group/delete/<chat_id>', methods=['DELETE'])
@token_required
def delete_group(username, chat_id):
    chats_data = load_json(CHATS_FILE, {})
    
    if chat_id not in chats_data:
        return jsonify({'error': 'Chat not found'}), 404
    
    chat = chats_data[chat_id]
    
    if chat.get('admin') != username:
        return jsonify({'error': 'Not authorized'}), 403
    
    group_name = chat.get('name', 'Group')
    participants = chat['participants']
    
    del chats_data[chat_id]
    save_json(CHATS_FILE, chats_data)
    
    for participant in participants:
        if participant in online_users:
            socketio.emit('group_deleted', {
                'chat_id': chat_id,
                'group_name': group_name,
                'deleted_by': username
            }, room=online_users[participant])
    
    return jsonify({'success': True})


# WebSocket Events

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    emit('connected', {'message': 'Connected to server'})

@socketio.on('ping')
def handle_ping():
    """Handle client ping to keep connection alive"""
    emit('pong')

@socketio.on('heartbeat')
def handle_heartbeat():
    """Handle heartbeat to maintain connection"""
    if request.sid in user_sessions:
        username = user_sessions[request.sid]
        emit('heartbeat_ack', {'username': username})
    else:
        emit('heartbeat_ack', {'status': 'ok'})

@socketio.on('authenticate')
def handle_authenticate(data):
    token = data.get('token')
    username = verify_token(token)
    
    if not username:
        emit('error', {'message': 'Invalid token'})
        disconnect()
        return
    
    # Store session
    user_sessions[request.sid] = username
    online_users[username] = request.sid
    
    print(f"User authenticated: {username}")
    
    # Notify friends
    user_friends = load_user_friends(username)
    
    for friend_username, status in user_friends.items():
        if status == 'accepted' and friend_username in online_users:
            socketio.emit('user_online', {
                'username': username
            }, room=online_users[friend_username])
    
    emit('authenticated', {'username': username})

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in user_sessions:
        username = user_sessions[request.sid]
        
        # Remove from online users
        if username in online_users:
            del online_users[username]
        del user_sessions[request.sid]
        
        print(f"User disconnected: {username}")
        
        # Notify friends
        user_friends = load_user_friends(username)
        
        for friend_username, status in user_friends.items():
            if status == 'accepted' and friend_username in online_users:
                socketio.emit('user_offline', {
                    'username': username
                }, room=online_users[friend_username])

@socketio.on('send_message')
def handle_send_message(data):
    """
    Messages are stored locally on client side for privacy.
    Server just broadcasts to participants.
    """
    try:
        if request.sid not in user_sessions:
            emit('error', {'message': 'Not authenticated'})
            return
        
        username = user_sessions[request.sid]
        chat_id = data.get('chat_id')
        message_text = data.get('message', '').strip()
        is_action = data.get('is_action', False)
        
        if not message_text:
            return
        
        chats_data = load_json(CHATS_FILE, {})
        
        if chat_id not in chats_data:
            emit('error', {'message': 'Chat not found'})
            return
        
        if username not in chats_data[chat_id]['participants']:
            emit('error', {'message': 'Not a participant'})
            return
        
        # Create message (not saved on server - privacy!)
        message = {
            'id': f"msg_{int(time.time() * 1000)}",
            'sender': username,
            'message': message_text,
            'timestamp': datetime.now().isoformat(),
            'is_action': is_action
        }
        
        # Broadcast to all participants (they save locally)
        for participant in chats_data[chat_id]['participants']:
            if participant in online_users:
                socketio.emit('new_message', {
                    'chat_id': chat_id,
                    'message': message
                }, room=online_users[participant])
        
        # Acknowledge message sent
        emit('message_sent', {'message_id': message['id'], 'status': 'success'})
        
    except Exception as e:
        print(f"Error in send_message: {e}")
        emit('error', {'message': 'Failed to send message'})

@socketio.on('typing')
def handle_typing(data):
    if request.sid not in user_sessions:
        return
    
    username = user_sessions[request.sid]
    chat_id = data.get('chat_id')
    
    chats_data = load_json(CHATS_FILE, {})
    if chat_id in chats_data and username in chats_data[chat_id]['participants']:
        for participant in chats_data[chat_id]['participants']:
            if participant != username and participant in online_users:
                socketio.emit('user_typing', {
                    'chat_id': chat_id,
                    'username': username
                }, room=online_users[participant])

if __name__ == '__main__':
    # Initialize files if they don't exist
    if not os.path.exists(USERS_INDEX_FILE):
        save_json(USERS_INDEX_FILE, {})
    if not os.path.exists(CHATS_FILE):
        save_json(CHATS_FILE, {})
    
    print(f"Starting NVDA Chat Server v2.0 on port 8080")
    print(f"Data directory: {DATA_PATH}")
    print(f"User folders: {USERS_DIR}")
    print("Messages stored locally on client devices for privacy")
    socketio.run(app, host='0.0.0.0', port=8080, debug=False)
