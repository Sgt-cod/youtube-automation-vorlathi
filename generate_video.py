import os
import json
import random
import re
import asyncio
from datetime import datetime
import requests
import feedparser
import edge_tts
from moviepy.editor import *
from google import generativeai as genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image, ImageDraw, ImageFont

CONFIG_FILE = 'config.json'
VIDEOS_DIR = 'videos'
ASSETS_DIR = 'assets'
VIDEO_TYPE = os.environ.get('VIDEO_TYPE', 'short')

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
PEXELS_API_KEY = os.environ.get('PEXELS_API_KEY')
YOUTUBE_CREDENTIALS = os.environ.get('YOUTUBE_CREDENTIALS')

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
    config = json.load(f)

def buscar_noticias():
    if config.get('tipo') != 'noticias':
        return None
    feeds = config.get('rss_feeds', [])
    todas_noticias = []
    for feed_url in feeds[:3]:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:3]:
                todas_noticias.append({
                    'titulo': entry.title,
                    'resumo': entry.get('summary', entry.title),
                    'link': entry.link
                })
        except:
            continue
    return random.choice(todas_noticias) if todas_noticias else None

def gerar_titulo_especifico(tema):
    """Gera título específico + keywords para busca"""
    prompt = f"""Baseado no tema "{tema}", crie um título ESPECÍFICO e palavras-chave para buscar imagens.

Retorne APENAS este JSON (sem texto adicional):
{{"titulo": "título específico aqui", "keywords": ["palavra1", "palavra2", "palavra3", "palavra4", "palavra5"]}}

Exemplo para "tecnologias futuristas":
{{"titulo": "Tecnologias Espaciais do Futuro", "keywords": ["space", "rocket", "satellite", "technology", "future"]}}"""
    
    response = model.generate_content(prompt)
    texto = response.text.strip().replace('```json', '').replace('```', '').strip()
    
    inicio = texto.find('{')
    fim = texto.rfind('}') + 1
    
    if inicio == -1 or fim == 0:
        return {"titulo": tema, "keywords": ["technology", "innovation", "future", "modern", "digital"]}
    
    try:
        return json.loads(texto[inicio:fim])
    except:
        return {"titulo": tema, "keywords": ["technology", "innovation", "future", "modern", "digital"]}

def gerar_roteiro(duracao_alvo, titulo, noticia=None):
    if duracao_alvo == 'short':
        palavras_alvo = 120
        tempo = '30-60 segundos'
    else:
        palavras_alvo = config.get('duracao_minutos', 10) * 150
        tempo = f"{config.get('duracao_minutos', 10)} minutos"
    
    # NOVO: Detectar persona alien
    persona = config.get('persona', None)
    
    if persona == 'alien_andromedano':
        prompt = f"""Você é Zyx, um ser extraterrestre da galáxia de Andrômeda que descobriu o YouTube.
        
Crie um script sobre: {titulo}

IMPORTANTE:
- Fale em PRIMEIRA PESSOA como um alien
- Tom: curioso, misterioso, levemente ameaçador mas fascinante
- Comece com: "Humanos... eu sou Zyx de Andrômeda..."
- Use termos como "vocês terráqueos", "seu planeta primitivo", "minha civilização avançada"
- Mencione diferenças entre nossos mundos
- Seja enigmático sobre suas intenções
- Finalize com algo tipo: "Em breve... vocês entenderão..."
- {tempo}, {palavras_alvo} palavras
- Texto puro, sem formatação

Escreva APENAS o roteiro para narração."""
    
    elif noticia:
        prompt = f"""Crie script para vídeo sobre: {titulo}
Resumo: {noticia['resumo']}
Requisitos: {tempo}, {palavras_alvo} palavras, tom noticioso, texto puro."""
    
    else:
        if duracao_alvo == 'short':
            prompt = f"""Crie script de SHORT sobre: {titulo}
Requisitos: {palavras_alvo} palavras, comece com "Você sabia que...", texto puro."""
        else:
            prompt = f"""Crie script sobre: {titulo}
Requisitos: {tempo}, {palavras_alvo} palavras, comece com "Olá!", texto puro."""
    
    response = model.generate_content(prompt)
    texto = response.text
    texto = re.sub(r'\*+', '', texto)
    texto = re.sub(r'#+\s', '', texto)
    texto = re.sub(r'^-\s', '', texto, flags=re.MULTILINE)
    texto = texto.replace('*', '').replace('#', '').replace('_', '').strip()
    return texto

async def criar_audio_async(texto, output_file):
    voz = config.get('voz', 'pt-BR-FranciscaNeural')
    for tentativa in range(3):
        try:
            communicate = edge_tts.Communicate(texto, voz, rate="+20%", pitch="+0Hz")
            await asyncio.wait_for(communicate.save(output_file), timeout=120)
            print(f"✅ Edge TTS (tent {tentativa + 1})")
            return
        except asyncio.TimeoutError:
            print(f"⏱️ Timeout {tentativa + 1}")
            if tentativa < 2:
                await asyncio.sleep(10)
        except Exception as e:
            print(f"⚠️ Erro {tentativa + 1}: {e}")
            if tentativa < 2:
                await asyncio.sleep(10)
    raise Exception("Edge TTS falhou")

def criar_audio(texto, output_file):
    print("🎙️ Criando narração...")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(criar_audio_async(texto, output_file))
        loop.close()
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            print(f"✅ Edge TTS: {os.path.getsize(output_file)} bytes")
            return output_file
    except Exception as e:
        print(f"❌ Edge TTS: {e}")
        print("🔄 Fallback gTTS...")
        from gtts import gTTS
        tts = gTTS(text=texto, lang='pt-br', slow=False)
        tts.save(output_file)
        print("⚠️ gTTS (robótico)")
    return output_file

def buscar_imagens_bing(termos, quantidade=10):
    """Busca imagens no Bing"""
    from urllib.parse import quote
    termo = ' '.join(termos[:3]) if isinstance(termos, list) else termos
    url = f'https://www.bing.com/images/search?q={quote(termo)}&first=1'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    imagens = []
    try:
        response = requests.get(url, headers=headers, timeout=15)
        urls = re.findall(r'"murl":"(.*?)"', response.text)
        for url_img in urls[:quantidade * 2]:
            try:
                img_response = requests.get(url_img, timeout=10, headers=headers)
                if img_response.status_code == 200:
                    temp_file = f'{ASSETS_DIR}/bing_{len(imagens)}.jpg'
                    with open(temp_file, 'wb') as f:
                        f.write(img_response.content)
                    imagens.append((temp_file, 'foto_local'))
                    if len(imagens) >= quantidade:
                        break
            except:
                continue
    except Exception as e:
        print(f"⚠️ Bing: {e}")
    return imagens

def buscar_midia_pexels(keywords, tipo='video', quantidade=1):
    headers = {'Authorization': PEXELS_API_KEY}
    if isinstance(keywords, str):
        keywords = [keywords]
    palavra_busca = ' '.join(keywords[:3])
    pagina = random.randint(1, 3)
    print(f"🔍 Pexels: '{palavra_busca}' (pág {pagina})")
    midias = []
    if tipo == 'video':
        orientacao = 'portrait' if VIDEO_TYPE == 'short' else 'landscape'
        url = f'https://api.pexels.com/videos/search?query={palavra_busca}&per_page=30&page={pagina}&orientation={orientacao}'
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                videos = response.json().get('videos', [])
                random.shuffle(videos)
                for video in videos:
                    for file in video['video_files']:
                        if VIDEO_TYPE == 'short':
                            if file.get('height', 0) > file.get('width', 0):
                                midias.append((file['link'], 'video'))
                                break
                        else:
                            if file.get('width', 0) >= 1280:
                                midias.append((file['link'], 'video'))
                                break
                    if len(midias) >= quantidade:
                        break
        except Exception as e:
            print(f"⚠️ Pexels vídeos: {e}")
    if len(midias) < quantidade:
        orientacao = 'portrait' if VIDEO_TYPE == 'short' else 'landscape'
        url = f'https://api.pexels.com/v1/search?query={palavra_busca}&per_page=50&page={pagina}&orientation={orientacao}'
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                fotos = response.json().get('photos', [])
                random.shuffle(fotos)
                for foto in fotos[:quantidade * 2]:
                    midias.append((foto['src']['large2x'], 'foto'))
        except Exception as e:
            print(f"⚠️ Pexels fotos: {e}")
    random.shuffle(midias)
    return midias[:quantidade]

def baixar_midia(url, filename):
    try:
        response = requests.get(url, stream=True, timeout=30)
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return filename
    except:
        return None

def criar_video_short(audio_path, midias, output_file, duracao):
    clips = []
    print(f"📹 {len(midias)} mídias para {duracao:.1f}s")
    if len(midias) < 4:
        midias = midias * 3
    duracao_por_midia = duracao / len(midias)
    for i, (midia_info, midia_tipo) in enumerate(midias):
        if not midia_info:
            continue
        try:
            if midia_tipo == 'foto_local':
                clip = ImageClip(midia_info).set_duration(duracao_por_midia)
                clip = clip.resize(height=1920)
                if clip.w > 1080:
                    clip = clip.crop(x_center=clip.w/2, width=1080, height=1920)
                clip = clip.resize(lambda t: 1 + 0.15 * (t / duracao_por_midia))
                clips.append(clip)
            elif midia_tipo == 'video':
                video_temp = f'{ASSETS_DIR}/v_{i}.mp4'
                if baixar_midia(midia_info, video_temp):
                    clip = VideoFileClip(video_temp, audio=False)
                    ratio = 9/16
                    if clip.w / clip.h > ratio:
                        new_w = int(clip.h * ratio)
                        clip = clip.crop(x_center=clip.w/2, width=new_w, height=clip.h)
                    else:
                        new_h = int(clip.w / ratio)
                        clip = clip.crop(y_center=clip.h/2, width=clip.w, height=new_h)
                    clip = clip.resize((1080, 1920))
                    clip = clip.set_duration(min(duracao_por_midia, clip.duration))
                    if i > 0:
                        clip = clip.crossfadein(0.3)
                    clips.append(clip)
            else:
                foto_temp = f'{ASSETS_DIR}/f_{i}.jpg'
                if baixar_midia(midia_info, foto_temp):
                    clip = ImageClip(foto_temp).set_duration(duracao_por_midia)
                    clip = clip.resize(height=1920)
                    if clip.w > 1080:
                        clip = clip.crop(x_center=clip.w/2, width=1080, height=1920)
                    clip = clip.resize(lambda t: 1 + 0.15 * (t / duracao_por_midia))
                    clips.append(clip)
        except Exception as e:
            print(f"⚠️ Mídia {i}: {e}")
    if not clips:
        return None
    video = concatenate_videoclips(clips, method="compose")
    video = video.set_duration(duracao)
    audio = AudioFileClip(audio_path)
    video = video.set_audio(audio)
    video.write_videofile(output_file, fps=30, codec='libx264', audio_codec='aac', preset='medium', bitrate='8000k')
    return output_file

def criar_video_long(audio_path, midias, output_file, duracao):
    clips = []
    duracao_por_midia = duracao / len(midias)
    for i, (midia_info, midia_tipo) in enumerate(midias):
        if not midia_info:
            continue
        try:
            if midia_tipo == 'foto_local':
                clip = ImageClip(midia_info).set_duration(duracao_por_midia)
                clip = clip.resize(height=1080)
                if clip.w < 1920:
                    clip = clip.resize(width=1920)
                clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1920, height=1080)
                clip = clip.resize(lambda t: 1 + 0.08 * (t / duracao_por_midia))
                if i > 0:
                    clip = clip.crossfadein(0.5)
                clips.append(clip)
            elif midia_tipo == 'video':
                video_temp = f'{ASSETS_DIR}/v_{i}.mp4'
                if baixar_midia(midia_info, video_temp):
                    clip = VideoFileClip(video_temp, audio=False)
                    clip = clip.resize(height=1080)
                    if clip.w < 1920:
                        clip = clip.resize(width=1920)
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1920, height=1080)
                    clip = clip.set_duration(min(duracao_por_midia, clip.duration))
                    if i > 0:
                        clip = clip.crossfadein(0.5)
                    clips.append(clip)
            else:
                foto_temp = f'{ASSETS_DIR}/f_{i}.jpg'
                if baixar_midia(midia_info, foto_temp):
                    clip = ImageClip(foto_temp).set_duration(duracao_por_midia)
                    clip = clip.resize(height=1080)
                    if clip.w < 1920:
                        clip = clip.resize(width=1920)
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1920, height=1080)
                    clip = clip.resize(lambda t: 1 + 0.08 * (t / duracao_por_midia))
                    if i > 0:
                        clip = clip.crossfadein(0.5)
                    clips.append(clip)
        except Exception as e:
            print(f"⚠️ Mídia {i}: {e}")
    if not clips:
        return None
    video = concatenate_videoclips(clips, method="compose")
    video = video.set_duration(duracao)
    audio = AudioFileClip(audio_path)
    video = video.set_audio(audio)
    video.write_videofile(output_file, fps=24, codec='libx264', audio_codec='aac', preset='medium', bitrate='5000k')
    return output_file

def criar_thumbnail(titulo, output_file, tipo='short'):
    tamanho = (1080, 1920) if tipo == 'short' else (1280, 720)
    font_size = 90 if tipo == 'short' else 80
    img = Image.new('RGB', tamanho, color=(25, 25, 45))
    draw = ImageDraw.Draw(img)
    for i in range(tamanho[1]):
        cor = (25 + i//25, 25 + i//35, 45 + i//20)
        draw.rectangle([(0, i), (tamanho[0], i+1)], fill=cor)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        font = ImageFont.load_default()
    palavras = titulo.split()[:8]
    linhas = []
    linha_atual = ""
    for palavra in palavras:
        teste = f"{linha_atual} {palavra}" if linha_atual else palavra
        if len(teste) < 20:
            linha_atual = teste
        else:
            linhas.append(linha_atual)
            linha_atual = palavra
    if linha_atual:
        linhas.append(linha_atual)
    y_start = tamanho[1] // 3
    for linha in linhas[:3]:
        bbox = draw.textbbox((0, 0), linha, font=font)
        w = bbox[2] - bbox[0]
        x = (tamanho[0] - w) // 2
        draw.text((x+3, y_start+3), linha, font=font, fill=(0, 0, 0))
        draw.text((x, y_start), linha, font=font, fill=(255, 255, 255))
        y_start += font_size + 20
    img.save(output_file, quality=95)
    return output_file

def fazer_upload_youtube(video_path, titulo, descricao, tags, thumbnail_path=None):
    creds_dict = json.loads(YOUTUBE_CREDENTIALS)
    credentials = Credentials.from_authorized_user_info(creds_dict)
    youtube = build('youtube', 'v3', credentials=credentials)
    body = {
        'snippet': {'title': titulo, 'description': descricao, 'tags': tags, 'categoryId': '27'},
        'status': {'privacyStatus': 'public', 'selfDeclaredMadeForKids': False}
    }
    media = MediaFileUpload(video_path, resumable=True)
    request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
    response = request.execute()
    video_id = response['id']
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumbnail_path)).execute()
            print("✅ Thumbnail enviada!")
        except Exception as e:
            print(f"⚠️ Thumbnail: {e}")
    return video_id

def main():
    print(f"{'📱' if VIDEO_TYPE == 'short' else '🎬'} Iniciando...")
    os.makedirs(VIDEOS_DIR, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    
    noticia = buscar_noticias()
    
    if noticia:
        titulo_video = noticia['titulo']
        keywords = titulo_video.split()[:5]
        print(f"📰 Notícia: {titulo_video}")
    else:
        tema = random.choice(config['temas'])
        print(f"📝 Tema: {tema}")
        info = gerar_titulo_especifico(tema)
        titulo_video = info['titulo']
        keywords = info['keywords']
        print(f"🎯 Título: {titulo_video}")
        print(f"🔍 Keywords: {', '.join(keywords)}")
    
    print("✍️ Gerando roteiro...")
    roteiro = gerar_roteiro(VIDEO_TYPE, titulo_video, noticia)
    
    audio_path = f'{ASSETS_DIR}/audio.mp3'
    criar_audio(roteiro, audio_path)
    
    audio_clip = AudioFileClip(audio_path)
    duracao = audio_clip.duration
    audio_clip.close()
    print(f"⏱️ {duracao:.1f}s")
    
    print("🖼️ Buscando mídias...")
    quantidade = 6 if VIDEO_TYPE == 'short' else max(50, int(duracao / 12))

if config.get('palavras_chave_fixas'):
    keywords = config.get('palavras_chave_fixas')
    print(f"🎯 Keywords fixas: {', '.join(keywords)}")
elif config.get('tipo') == 'noticias' and config.get('fonte_midias') == 'bing':
    midias = buscar_imagens_bing(keywords, quantidade)
else:

  if config.get('palavras_chave_fixas'):
    # Usar APENAS as keywords fixas
    midias = buscar_midia_pexels(config['palavras_chave_fixas'], tipo='video', quantidade=quantidade)
    
    if config.get('tipo') == 'noticias' and config.get('fonte_midias') == 'bing':
        midias = buscar_imagens_bing(keywords, quantidade)
    else:
        midias = buscar_midia_pexels(keywords, tipo='video', quantidade=quantidade)
    
    if len(midias) < 3:
        print("⚠️ Poucas mídias, complementando...")
        midias.extend(buscar_midia_pexels(['nature landscape'], tipo='foto', quantidade=5))
    
    print(f"✅ {len(midias)} mídias")
    
    print("🎥 Montando...")
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    video_path = f'{VIDEOS_DIR}/{VIDEO_TYPE}_{timestamp}.mp4'
    
    if VIDEO_TYPE == 'short':
        resultado = criar_video_short(audio_path, midias, video_path, duracao)
    else:
        resultado = criar_video_long(audio_path, midias, video_path, duracao)
    
    if not resultado:
        print("❌ Erro")
return
    
   # thumbnail_path = f'{VIDEOS_DIR}/thumb_{timestamp}.jpg'
  #  criar_thumbnail(titulo_video, thumbnail_path, VIDEO_TYPE)
    thumbnail_path = None
    
    titulo = titulo_video[:60] if len(titulo_video) <= 60 else titulo_video[:57] + '...'
    if VIDEO_TYPE == 'short':
        titulo += ' #shorts'
    descricao = roteiro[:300] + '...\n\n🔔 Inscreva-se!\n#' + ('shorts' if VIDEO_TYPE == 'short' else 'curiosidades')
    tags = ['curiosidades', 'fatos'] if not noticia else ['noticias', 'informacao']
    if VIDEO_TYPE == 'short':
        tags.append('shorts')
    
    print("📤 Upload...")
    video_id = fazer_upload_youtube(video_path, titulo, descricao, tags, thumbnail_path)
    
    url = f'https://youtube.com/{"shorts" if VIDEO_TYPE == "short" else "watch?v="}{video_id}'
    
    log_entry = {
        'data': datetime.now().isoformat(),
        'tipo': VIDEO_TYPE,
        'tema': titulo_video,
        'titulo': titulo,
        'duracao': duracao,
        'video_id': video_id,
        'url': url
    }
    
    log_file = 'videos_gerados.json'
    logs = []
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = json.load(f)
    logs.append(log_entry)
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Publicado!\n🔗 {url}")
    
    for file in os.listdir(ASSETS_DIR):
        try:
            os.remove(os.path.join(ASSETS_DIR, file))
        except:
            pass

if __name__ == '__main__':
    main()
