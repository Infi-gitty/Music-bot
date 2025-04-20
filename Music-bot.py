import datetime
import random
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import view
import yt_dlp
import asyncio
import os
import aiohttp
import urllib.parse
import traceback
import json
import logging
from youtubesearchpython import Suggestions

TOKEN = "YOUR_TOKEN_HERE" #-----paste your bot token which you copied from discord developer when you created the bot------
print(f"🔑 TOKEN loaded: {TOKEN is not None}")

intents = discord.Intents.default()
intents.message_content = False
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
queue = {}
current_song = {}

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': 'True',
    'quiet': True,
    'default_search': 'ytsearch',
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# --- Autocomplete Function ---
async def get_music_suggestions(interaction: discord.Interaction, current: str):
    if not current:
        return []

    try:
        url = f"http://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={urllib.parse.quote(current)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                text = await resp.text()
                data = json.loads(text)
                suggestions = data[1]
                return [app_commands.Choice(name=s[:100], value=s[:100]) for s in suggestions[:5]]
    except Exception as e:
        print(f"Autocomplete error: {e}")
        return []


# --- Interactive Buttons ---
class MusicView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⏯️ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            if vc.is_playing():
                vc.pause()
            elif vc.is_paused():
                vc.resume()
        await interaction.response.defer()

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.danger)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await interaction.response.defer()

    @discord.ui.button(label="🔀 Shuffle", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild.id in queue and len(queue[interaction.guild.id]) > 1:
            random.shuffle(queue[interaction.guild.id])
            await interaction.response.send_message("Queue shuffled.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to shuffle.", ephemeral=True)

    @discord.ui.button(label="📄 Queue", style=discord.ButtonStyle.secondary)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        if guild_id in queue and queue[guild_id]:
            q = "\n".join([f"{i+1}. {s[1]}" for i, s in enumerate(queue[guild_id])])
            embed = discord.Embed(title="Current Queue", description=q, color=discord.Color.blurple())
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("Queue is empty.", ephemeral=True)

# --- Now Playing Embed ---
async def send_now_playing(interaction, url, title, thumbnail, duration):
    bar = "▬" * 10
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"[{title}]({url})",
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url=thumbnail)
    embed.add_field(name="Progress", value=f"`0:00 [{bar}] {str(datetime.timedelta(seconds=duration))}`", inline=False)

    view = MusicView()
    view.add_item(discord.ui.Button(label="▶️ Watch on YouTube", url=url))
    await interaction.followup.send(embed=embed, view=view)

# --- Music Playback ---
async def play_next(interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if guild_id in queue and queue[guild_id]:
        url, title, thumbnail, duration = queue[guild_id].pop(0)
        current_song[guild_id] = (url, title, thumbnail, duration)

        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)
            audio_url = info['url']

        source = await discord.FFmpegOpusAudio.from_probe(audio_url, **FFMPEG_OPTIONS)
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction), bot.loop))
        await send_now_playing(interaction, url, title, thumbnail, duration)
    else:
        current_song[guild_id] = None
        await asyncio.sleep(60)
        if not vc.is_playing() and not vc.is_paused():
            await vc.disconnect()

# --- SLASH COMMANDS ---

@bot.tree.command(name="play", description="Play a YouTube song")
@app_commands.describe(query="Search for a song or paste YouTube URL")
@app_commands.autocomplete(query=get_music_suggestions)
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("You need to be in a voice channel.", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if not vc:
        vc = await channel.connect()
    elif vc.channel != channel:
        await vc.move_to(channel)

    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if 'entries' in info:
            info = info['entries'][0]
        url = info['webpage_url']
        title = info['title']
        thumbnail = info.get("thumbnail", "")
        duration = info.get("duration", 0)

    queue.setdefault(interaction.guild.id, []).append((url, title, thumbnail, duration))

    if not vc.is_playing():
        await play_next(interaction)
    else:
        await interaction.followup.send(f"🎶 Added to queue: **{title}**")

@bot.tree.command(name="stop", description="Stop music and clear queue")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
    queue[interaction.guild.id] = []
    current_song[interaction.guild.id] = None
    await interaction.followup.send("⛔ Stopped and left the channel.")

@bot.tree.command(name="pause", description="Pause playback")
async def pause(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.followup.send("⏸️ Paused.")

@bot.tree.command(name="resume", description="Resume playback")
async def resume(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.followup.send("▶️ Resumed.")

@bot.tree.command(name="leave", description="Disconnect from voice channel")
async def leave(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.followup.send("👋 Left the voice channel.")
    else:
        await interaction.followup.send("Not connected to any voice channel.", ephemeral=True)

# --- On Ready ---
@bot.event
async def on_ready():
    print(f'{bot.user} has connected')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f"Sync Error: {e}")

bot.run(TOKEN)