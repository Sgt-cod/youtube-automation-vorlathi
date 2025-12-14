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
model = genai.GenerativeModel('gemini-2.0-flash-exp')

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
    prompt = f"""Baseado no tema "{tema}", crie um título ESPECÍFICO e palavras-chave.
Retorne APENAS JSON: {{"titulo": "título aqui", "keywords": ["palavra1", "palavra2", "palavra3", "palavra4", "palavra5"]}}"""
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
    persona = config.get('persona', None)
    if persona == 'alien_solkara':
        prompt = f"""Você é Vorlathi, do planeta Solkara (Kepler-1649c).
Script sobre: {titulo}
- Primeira pessoa como alien
- Tom: misterioso, fascinante
- Comece: "Humanos... eu sou Vorlathi, do planeta Solkara..."
- Mencione que terráqueos chamam de "Kepler-1649c"
- Use: "vocês terráqueos", "minha civilização de Solkara"
- Enigmático sobre intenções
- Finalize: "Logo vocês compreenderão..."
- {tempo}, {palavras_alvo} palavras, texto puro"""
    elif noticia:
        prompt = f"""Script sobre: {titulo}
Resumo: {noticia['resumo']}
{tempo}, {palavras_alvo} palavras, noticioso, texto puro."""
    else:
        if duracao_alvo == 'short':
            prompt = f"""Script SHORT: {titulo}
{palavras_alvo} palavras, comece "Você sabia que...", texto puro."""
        else:
            prompt = f"""Script: {titulo}
{tempo}, {palavras_alvo} palavras, comece "Olá!", texto puro."""
    response = model.generate_content(prompt)
    texto = response.text
    texto = re.sub(r'\*+', '', texto)
    texto = re.sub(r'#+\s', '', texto)
    texto = re.sub(r'^-\s', '', texto, flags=re.MULTILINE)
    return texto.replace('*', '').replace('#', '').replace('_', '').strip()

async def criar_audio_async(texto, output_file):
    voz = config.get('voz', 'pt-BR-FranciscaNeural')
    for tentativa in range(3):
        try:
            communicate = edge_tts.Communicate(texto, voz, rate="+0%", pitch="+0Hz")
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
        print("⚠️ gTTS")
    return output_file

def extrair_keywords_do_texto(texto):
    prompt = f"""Extraia 3-5 palavras-chave em INGLÊS para buscar imagens/vídeos:
"{texto[:200]}"
Retorne APENAS palavras separadas por vírgula."""
    try:
        response = model.generate_content(prompt)
        keywords = [k.strip() for k in response.text.strip().split(',')]
        return keywords[:5]
    except:
        palavras = texto.lower().split()
        return [p for p in palavras if len(p) > 4][:3]

def analisar_roteiro_e_buscar_midias(roteiro, duracao_audio, usar_bing=False):
    print("📋 Analisando roteiro para sincronização...")
    segmentos = re.split(r'[.!?]\s+', roteiro)
    segmentos = [s.strip() for s in segmentos if len(s.strip()) > 20]
    print(f"   {len(segmentos)} segmentos encontrados")
    palavras_total = len(roteiro.split())
    palavras_por_segundo = palavras_total / duracao_audio
    segmentos_com_tempo = []
    tempo_atual = 0
    for segmento in segmentos:
        palavras_segmento = len(segmento.split())
        duracao_segmento = palavras_segmento / palavras_por_segundo
        keywords = extrair_keywords_do_texto(segmento)
        segmentos_com_tempo.append({
            'texto': segmento[:50],
            'inicio': tempo_atual,
            'duracao': duracao_segmento,
            'keywords': keywords
        })
        tempo_atual += duracao_segmento
    midias_sincronizadas = []
    for i, seg in enumerate(segmentos_com_tempo):
        print(f"🔍 Seg {i+1}: '{seg['texto']}...' → {seg['keywords']}")
        if usar_bing:
            midia = buscar_imagens_bing(seg['keywords'], quantidade=1)
        else:
            midia = buscar_midia_pexels(seg['keywords'], tipo='video', quantidade=1)
        if midia and len(midia) > 0:
            midias_sincronizadas.append({
                'midia': midia[0],
                'inicio': seg['inicio'],
                'duracao': seg['duracao']
            })
        else:
            print(f"   ⚠️ Sem mídia para seg {i+1}")
    print(f"✅ {len(midias_sincronizadas)} mídias sincronizadas")
    return midias_sincronizadas

def buscar_imagens_bing(termos, quantidade=10):
    from urllib.parse import quote
    termo = ' '.join(termos[:3]) if isinstance(termos, list) else str(termos)
    url = f'https://www.bing.com/images/search?q={quote(termo)}&first=1'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    midias = []
    try:
        response = requests.get(url, headers=headers, timeout=15)
        urls = re.findall(r'"murl":"(.*?)"', response.text)
        for url_img in urls[:quantidade * 2]:
            try:
                img_response = requests.get(url_img, timeout=10, headers=headers)
                if img_response.status_code == 200:
                    temp_file = f'{ASSETS_DIR}/bing_{len(midias)}.jpg'
                    with open(temp_file, 'wb') as f:
                        f.write(img_response.content)
                    midias.append((temp_file, 'foto_local'))
                    if len(midias) >= quantidade:
                        break
            except:
                continue
    except Exception as e:
        print(f"⚠️ Bing: {e}")
    print(f"   Bing: {len(midias)} imagens")
    return midias

def buscar_midia_pexels(keywords, tipo='video', quantidade=1):
    headers = {'Authorization': PEXELS_API_KEY}
    if isinstance(keywords, str):
        keywords = [keywords]
    palavra_busca = ' '.join(keywords[:3])
    pagina = random.randint(1, 3)
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

def criar_video_short_sincronizado(audio_path, midias_sincronizadas, output_file, duracao_total):
    print(f"📹 Criando short sincronizado com {len(midias_sincronizadas)} mídias")
    clips = []
    for i, item in enumerate(midias_sincronizadas):
        midia_info, midia_tipo = item['midia']
        inicio = item['inicio']
        duracao_clip = item['duracao']
        if not midia_info:
            continue
        try:
            if midia_tipo == 'foto_local':
                clip = ImageClip(midia_info).set_duration(duracao_clip)
                clip = clip.resize(height=1920)
                if clip.w > 1080:
                    clip = clip.crop(x_center=clip.w/2, width=1080, height=1920)
                clip = clip.resize(lambda t: 1 + 0.1 * (t / duracao_clip))
                clip = clip.set_start(inicio)
                clips.append(clip)
            elif midia_tipo == 'video':
                video_temp = f'{ASSETS_DIR}/v_{i}.mp4'
                if baixar_midia(midia_info, video_temp):
                    vclip = VideoFileClip(video_temp, audio=False)
                    ratio = 9/16
                    if vclip.w / vclip.h > ratio:
                        new_w = int(vclip.h * ratio)
                        vclip = vclip.crop(x_center=vclip.w/2, width=new_w, height=vclip.h)
                    else:
                        new_h = int(vclip.w / ratio)
                        vclip = vclip.crop(y_center=vclip.h/2, width=vclip.w, height=new_h)
                    vclip = vclip.resize((1080, 1920))
                    vclip = vclip.set_duration(min(duracao_clip, vclip.duration))
                    vclip = vclip.set_start(inicio)
                    clips.append(vclip)
            else:
                foto_temp = f'{ASSETS_DIR}/f_{i}.jpg'
                if baixar_midia(midia_info, foto_temp):
                    clip = ImageClip(foto_temp).set_duration(duracao_clip)
                    clip = clip.resize(height=1920)
                    if clip.w > 1080:
                        clip = clip.crop(x_center=clip.w/2, width=1080, height=1920)
                    clip = clip.resize(lambda t: 1 + 0.1 * (t / duracao_clip))
                    clip = clip.set_start(inicio)
                    clips.append(clip)
        except Exception as e:
            print(f"⚠️ Erro mídia {i}: {e}")
    if not clips:
        return None
    video = CompositeVideoClip(clips, size=(1080, 1920))
    video = video.set_duration(duracao_total)
    audio = AudioFileClip(audio_path)
    video = video.set_audio(audio)
    video.write_videofile(output_file, fps=30, codec='libx264', audio_codec='aac', preset='medium', bitrate='8000k')
    return output_file

def criar_video_long_sincronizado(audio_path, midias_sincronizadas, output_file, duracao_total):
    print(f"📹 Criando long sincronizado com {len(midias_sincronizadas)} mídias")
    clips = []
    for i, item in enumerate(midias_sincronizadas):
        midia_info, midia_tipo = item['midia']
        inicio = item['inicio']
        duracao_clip = item['duracao']
        if not midia_info:
            continue
        try:
            if midia_tipo == 'foto_local':
                clip = ImageClip(midia_info).set_duration(duracao_clip)
                clip = clip.resize(height=1080)
                if clip.w < 1920:
                    clip = clip.resize(width=1920)
                clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1920, height=1080)
                clip = clip.resize(lambda t: 1 + 0.05 * (t / duracao_clip))
                clip = clip.set_start(inicio)
                clips.append(clip)
            elif midia_tipo == 'video':
                video_temp = f'{ASSETS_DIR}/v_{i}.mp4'
                if baixar_midia(midia_info, video_temp):
                    vclip = VideoFileClip(video_temp, audio=False)
                    vclip = vclip.resize(height=1080)
                    if vclip.w < 1920:
                        vclip = vclip.resize(width=1920)
                    vclip = vclip.crop(x_center=vclip.w/2, y_center=vclip.h/2, width=1920, height=1080)
                    vclip = vclip.set_duration(min(duracao_clip, vclip.duration))
                    vclip = vclip.set_start(inicio)
                    clips.append(vclip)
            else:
                foto_temp = f'{ASSETS_DIR}/f_{i}.jpg'
                if baixar_midia(midia_info, foto_temp):
                    clip = ImageClip(foto_temp).set_duration(duracao_clip)
                    clip = clip.resize(height=1080)
                    if clip.w < 1920:
                        clip = clip.resize(width=1920)
                    clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=1920, height=1080)
                    clip = clip.resize(lambda t: 1 + 0.05 * (t / duracao_clip))
                    clip = clip.set_start(inicio)
                    clips.append(clip)
        except Exception as e:
            print(f"⚠️ Erro mídia {i}: {e}")
    if not clips:
        return None
    video = CompositeVideoClip(clips, size=(1920, 1080))
    video = video.set_duration(duracao_total)
    audio = AudioFileClip(audio_path)
    video = video.set_audio(audio)
    video.write_videofile(output_file, fps=24, codec='libx264', audio_codec='aac', preset='medium', bitrate='5000k')
    return output_file

def fazer_upload_youtube(video_path, titulo, descricao, tags):
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
    return response['id']

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
    usar_bing = config.get('tipo') == 'noticias' and config.get('fonte_midias') == 'bing'
    if usar_bing:
        print("🌐 Modo: BING (notícias)")
    else:
        print("📸 Modo: PEXELS")
    if config.get('palavras_chave_fixas'):
        keywords_busca = config.get('palavras_chave_fixas')
        print(f"🎯 Keywords fixas: {', '.join(keywords_busca)}")
    else:
        keywords_busca = keywords
    midias_sincronizadas = analisar_roteiro_e_buscar_midias(roteiro, duracao, usar_bing)
    if len(midias_sincronizadas) < 3:
        print("⚠️ Poucas mídias, complementando...")
        extras = buscar_midia_pexels(['nature landscape'], tipo='foto', quantidade=5)
        tempo_restante = duracao - sum([m['duracao'] for m in midias_sincronizadas])
        duracao_extra = tempo_restante / len(extras) if extras else 0
        for extra in extras:
            midias_sincronizadas.append({
                'midia': extra,
                'inicio': duracao - tempo_restante,
                'duracao': duracao_extra
            })
            tempo_restante -= duracao_extra
    print("🎥 Montando vídeo sincronizado...")
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    video_path = f'{VIDEOS_DIR}/{VIDEO_TYPE}_{timestamp}.mp4'
    if VIDEO_TYPE == 'short':
        resultado = criar_video_short_sincronizado(audio_path, midias_sincronizadas, video_path, duracao)
    else:
        resultado = criar_video_long_sincronizado(audio_path, midias_sincronizadas, video_path, duracao)
    if not resultado:
        print("❌ Erro")
        return
    titulo = titulo_video[:60] if len(titulo_video) <= 60 else titulo_video[:57] + '...'
    if VIDEO_TYPE == 'short':
        titulo += ' #shorts'
    descricao = roteiro[:300] + '...\n\n🔔 Inscreva-se!\n#' + ('shorts' if VIDEO_TYPE == 'short' else 'curiosidades')
    tags = ['curiosidades', 'fatos'] if not noticia else ['noticias', 'informacao']
    if VIDEO_TYPE == 'short':
        tags.append('shorts')
    print("📤 Upload...")
    video_id = fazer_upload_youtube(video_path, titulo, descricao, tags)
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
