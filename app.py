import os
import re
import tempfile
import time
import subprocess
import yt_dlp
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def check_nodejs():
    try:
        result = subprocess.run(['node', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"✅ Node.js detected: {result.stdout.strip()}")
            return True
    except:
        pass
    return False

HAS_NODEJS = check_nodejs()
download_progress = {}

def progress_hook(session_id):
    def hook(d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d and d['total_bytes'] > 0:
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
                download_progress[session_id] = {
                    'status': 'downloading',
                    'percent': round(percent, 1),
                    'speed': d.get('speed', 0),
                    'eta': d.get('eta', 0),
                }
        elif d['status'] == 'finished':
            download_progress[session_id] = {'status': 'processing', 'percent': 100}
    return hook

def sanitize_filename(title):
    return re.sub(r'[\\/*?:"<>|]', "", title)[:100]

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'running', 'nodejs_available': HAS_NODEJS})

@app.route('/api/info', methods=['POST'])
def video_info():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    ydl_opts = {'quiet': True, 'no_warnings': True}
    if HAS_NODEJS:
        ydl_opts['compat_opts'] = ['no-youtube-unavailable-formats']
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
            
            heights = set()
            for f in formats:
                if f.get('vcodec') != 'none' and f.get('height'):
                    heights.add(f['height'])
            
            available_qualities = sorted([h for h in heights if h >= 144], reverse=True)
            if not available_qualities:
                available_qualities = [1080, 720, 480, 360, 144]
            
            return jsonify({
                'title': info.get('title', 'Untitled'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'channel': info.get('uploader', 'Unknown'),
                'qualities': available_qualities,
                'nodejs_available': HAS_NODEJS
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download', methods=['POST'])
def download():
    data = request.get_json()
    url = data.get('url')
    mode = data.get('mode')
    quality = data.get('quality')
    session_id = data.get('session_id', str(time.time()))

    if not url:
        return jsonify({'error': 'No URL'}), 400

    download_dir = tempfile.mkdtemp()
    
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = sanitize_filename(info.get('title', 'video'))
        
        base_opts = {
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [progress_hook(session_id)],
        }
        
        if HAS_NODEJS:
            base_opts['compat_opts'] = ['no-youtube-unavailable-formats']
        
        if mode == 'video':
            ydl_opts = {
                **base_opts,
                'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]',
                'merge_output_format': 'mp4',
            }
            filename = f"{title}_{quality}p.mp4"
            mimetype = 'video/mp4'
        else:
            ydl_opts = {
                **base_opts,
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
            filename = f"{title}_audio.mp3"
            mimetype = 'audio/mpeg'
        
        download_progress[session_id] = {'status': 'starting', 'percent': 0}
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        downloaded_file = None
        for f in os.listdir(download_dir):
            if (mode == 'video' and f.endswith('.mp4')) or (mode == 'audio' and f.endswith('.mp3')):
                downloaded_file = os.path.join(download_dir, f)
                break
        
        if not downloaded_file:
            return jsonify({'error': 'File not found'}), 500
        
        response = send_file(downloaded_file, as_attachment=True, download_name=filename, mimetype=mimetype)
        
        @response.call_on_close
        def cleanup():
            try:
                time.sleep(0.5)
                if os.path.exists(downloaded_file):
                    os.remove(downloaded_file)
                os.rmdir(download_dir)
                if session_id in download_progress:
                    del download_progress[session_id]
            except:
                pass
        
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/progress/<session_id>')
def get_progress(session_id):
    progress = download_progress.get(session_id, {'status': 'not_found', 'percent': 0})
    return jsonify(progress)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)