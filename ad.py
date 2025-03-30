import streamlit as st

st.set_page_config(
    page_title="After Dark",
    page_icon="🌙"
)

import aiohttp
import asyncio
import urllib.parse
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import os
import uuid
import json
import zipfile
import re
import io
import asyncio.subprocess
import logging

debug_logs = []
debug_box = st.sidebar.empty()

def update_debug_box():
    """Update the sidebar debug box with the current debug logs."""
    html_content = (
        "<div style='height:250px; overflow:auto; background: linear-gradient(135deg, #2c3e50, #4ca1af); "
        "padding:10px; border:2px solid #1abc9c; border-radius:8px; color:#ecf0f1; font-family:monospace;'>"
        + "<br>".join(debug_logs)
        + "</div>"
    )
    debug_box.markdown(html_content, unsafe_allow_html=True)

def debug_log(message: str):
    global debug_logs
    debug_logs.append(message)
    print(message)
    update_debug_box()

async def get_webpage_content(url, session):
    async with session.get(url, allow_redirects=True) as response:
        return await response.text(), str(response.url), response.status

def extract_album_links(page_content):
    soup = BeautifulSoup(page_content, 'html.parser')
    links = set()
    for a_tag in soup.find_all('a', class_='album-link'):
        href = a_tag.get('href')
        if href and href.startswith("https://www.erome.com/a/"):
            links.add(href)
    return list(links)

# --- New Functions for Bunkr Image Gallery ---

async def get_album_links_from_search(username: str, session: aiohttp.ClientSession, page: int = 1) -> list:
    search_url = f"https://bunkr-albums.io/?search={urllib.parse.quote(username)}&page={page}"
    debug_log(f"[DEBUG] Searching albums for username: '{username}' on page {page} using URL: {search_url}")
    try:
        async with session.get(search_url) as response:
            if response.status != 200:
                debug_log(f"[DEBUG] Received {response.status} for search query. Skipping page {page}.")
                return []
            text = await response.text()
        soup = BeautifulSoup(text, 'html.parser')
        album_tags = soup.find_all('a', href=lambda h: h and h.startswith("https://bunkr.cr/a/"))
        album_links = []
        for tag in album_tags:
            album_link = tag.get('href')
            album_links.append(album_link)
            debug_log(f"[DEBUG] Found album link: {album_link}")
        debug_log(f"[DEBUG] Total album links found on page {page}: {len(album_links)}")
        return album_links
    except Exception as e:
        debug_log(f"[DEBUG] Error fetching albums for {username} on page {page}: {e}")
        return []

async def get_all_album_links_from_search(username: str, session: aiohttp.ClientSession) -> list:
    all_links = []
    page = 1
    while True:
        links = await get_album_links_from_search(username, session, page)
        if not links:
            debug_log(f"[DEBUG] No album links found on page {page}. Ending pagination.")
            break
        all_links.extend(links)
        search_url = f"https://bunkr-albums.io/?search={urllib.parse.quote(username)}&page={page}"
        async with session.get(search_url) as response:
            text = await response.text()
        soup = BeautifulSoup(text, 'html.parser')
        next_page = soup.find('a', href=re.compile(rf"\?search={re.escape(username)}&page={page+1}"), class_="btn btn-sm btn-main")
        if not next_page:
            debug_log(f"[DEBUG] No pagination link found for page {page+1}.")
            break
        page += 1
    debug_log(f"[DEBUG] Total album links collected from all pages (including duplicates): {len(all_links)}")
    return all_links

async def get_image_links_from_album(album_url: str, session: aiohttp.ClientSession) -> list:
    debug_log(f"[DEBUG] Fetching album page: {album_url}")
    try:
        async with session.get(album_url) as response:
            if response.status != 200:
                debug_log(f"[DEBUG] Received {response.status} for album URL {album_url}. Skipping.")
                return []
            text = await response.text()
        soup = BeautifulSoup(text, 'html.parser')
        links = []
        for link in soup.find_all('a', attrs={'aria-label': 'download'}, href=True):
            href = link.get('href')
            if href.startswith("/f/"):
                full_link = "https://bunkr.cr" + href
                links.append(full_link)
                debug_log(f"[DEBUG] Found image page link: {full_link}")
            elif href.startswith("https://bunkr.cr/f/"):
                links.append(href)
                debug_log(f"[DEBUG] Found image page link: {href}")
        debug_log(f"[DEBUG] Total image page links found on album page: {len(links)}")
        return links
    except Exception as e:
        debug_log(f"[DEBUG] Error fetching images from album URL {album_url}: {e}")
        return []

async def get_image_url_from_linkk(link: str, session: aiohttp.ClientSession) -> str:
    debug_log(f"[DEBUG] Opening image page link: {link}")
    try:
        async with session.get(link) as response:
            if response.status != 200:
                debug_log(f"[DEBUG] Received {response.status} for link: {link}. Skipping.")
                return None
            text = await response.text()
    except Exception as e:
        debug_log(f"[DEBUG] Error fetching image page {link}: {e}")
        return None

    soup = BeautifulSoup(text, 'html.parser')
    img_tag = soup.find('img', class_=lambda x: x and "object-cover" in x)
    if img_tag:
        image_url = img_tag.get('src')
        debug_log(f"[DEBUG] Found image URL: {image_url} for page link: {link}")
        try:
            head_task = session.head(image_url)
            head_response = await asyncio.gather(head_task)
            if head_response[0].status != 200:
                debug_log(f"[DEBUG] HEAD request for image URL {image_url} returned status {head_response[0].status}. Skipping.")
                return None
        except Exception as e:
            debug_log(f"[DEBUG] Error during HEAD check for image URL {image_url}: {e}. Skipping.")
            return None
        return image_url
    debug_log(f"[DEBUG] No image tag found on page: {link}")
    return None

async def fetch_bunkr_gallery_images(username: str) -> list:
    async with aiohttp.ClientSession() as session:
        album_links = await get_all_album_links_from_search(username, session)
        tasks = []
        for album in album_links:
            img_page_links = await get_image_links_from_album(album, session)
            for link in img_page_links:
                tasks.append(get_image_url_from_linkk(link, session))
        results = await asyncio.gather(*tasks)
        # Filter out any None values
        image_urls = [url for url in results if url is not None]
        # Remove duplicates and ignore URLs containing '/thumb/'
        unique_urls = list({url for url in image_urls if "/thumb/" not in url})
        return unique_urls

async def fetch_all_album_pages(username, max_pages=10):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for page in range(1, max_pages + 1):
            search_url = f"https://www.erome.com/search?q={username}&page={page}"
            tasks.append(get_webpage_content(search_url, session))
        pages_content = await asyncio.gather(*tasks, return_exceptions=True)
        all_links = set()
        for page_content, _, _ in filter(lambda x: not isinstance(x, Exception), pages_content):
            if page_content:
                links = extract_album_links(page_content)
                all_links.update(links)
        return list(all_links)

async def fetch_image_urls(album_url, session):
    try:
        page_content, base_url, _ = await get_webpage_content(album_url, session)
        soup = BeautifulSoup(page_content, 'html.parser')
        images = [
            urljoin(base_url, img['data-src'])
            for img in soup.find_all('div', class_='img')
            if img.get('data-src')
        ]
        return images
    except Exception as e:
        st.error(f"Error fetching images from {album_url}: {e}")
        return []

# New function: Fetch all Erome image URLs without downloading them
async def fetch_all_erome_image_urls(album_urls):
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        fetch_tasks = [fetch_image_urls(url, session) for url in album_urls]
        all_images = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        all_image_urls = [img_url for images in all_images if isinstance(images, list) for img_url in images]
        return all_image_urls

def zip_images(image_paths, username):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for image_path in image_paths:
            image_name = os.path.basename(image_path)
            zip_file.write(image_path, arcname=image_name)
    zip_buffer.seek(0)
    return zip_buffer

def parse_links_and_titles(page_content, pattern, title_class):
    soup = BeautifulSoup(page_content, 'html.parser')
    links = [a['href'] for a in soup.find_all('a', href=True) if re.match(pattern, a['href'])]
    titles = [span.get_text() for span in soup.find_all('span', class_=title_class)]
    return links, titles

async def fetch_search_results(query, page):
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://bunkr-albums.io/?search={encoded_query}&page={page}"
    async with aiohttp.ClientSession() as session:
        async with session.get(search_url) as response:
            if response.status != 200:
                return None
            return await response.text()

async def fetch_all_pages(query, pattern, title_class, max_pages=10):
    tasks = [fetch_search_results(query, page) for page in range(1, max_pages + 1)]
    pages_content = await asyncio.gather(*tasks)
    all_links = []
    all_titles = []
    for page_content in pages_content:
        if page_content:
            links, titles = parse_links_and_titles(page_content, pattern, title_class)
            all_links.extend(links)
            all_titles.extend(titles)
    return all_links, all_titles

async def search_bunkr_links(input_query):
    st.write(f"Fetching content for '{input_query}'. This may take a few seconds...")
    search_pattern = r'https://bunkr\.cr/a/[\w\d-]+'
    title_class = 'truncate'
    all_links, all_titles = await fetch_all_pages(input_query, search_pattern, title_class)
    if not all_links:
        st.write(f"🚫 No leaks found for '{input_query}'.")
        return
    st.write(f"🔗 Found {len(all_links)} albums:")
    cols = st.columns(3)
    for i, (title, link) in enumerate(zip(all_titles, all_links)):
        with cols[i % 3]:
            st.markdown(f"**{title}**")
            st.markdown(f"[Link]({link})")

async def fetch_fapello_page_media(page_url: str, session: aiohttp.ClientSession, username: str) -> dict:
    try:
        content, base_url, status = await get_webpage_content(page_url, session)
        if status != 200:
            debug_log(f"[DEBUG] Failed to fetch {page_url} with status {status}")
            return {}
        soup = BeautifulSoup(content, 'html.parser')
        # Get images from this page
        page_images = [img['src'] for img in soup.find_all('img', src=True)
                       if img['src'].startswith("https://fapello.com/content/") and f"/{username}/" in img['src']]
        # Get videos from this page
        video_tags = soup.find_all('source', type="video/mp4", src=True)
        page_videos = [vid['src'] for vid in video_tags
                       if vid['src'].startswith("https://cdn.fapello.com/content/") and 
                          (vid['src'].endswith(".mp4") or vid['src'].endswith(".m4v"))]
        debug_log(f"[DEBUG] {page_url}: Found {len(page_images)} images and {len(page_videos)} videos for user {username}")
        return {"images": page_images, "videos": page_videos}
    except Exception as e:
        debug_log(f"[DEBUG] Exception in fetching {page_url}: {e}")
        return {}

async def fetch_fapello_album_media(album_url: str) -> dict:
    media = {"images": [], "videos": []}
    parsed = urllib.parse.urlparse(album_url)
    path_parts = parsed.path.strip("/").split("/")
    username = path_parts[0] if path_parts else ""
    if not username:
        debug_log("[DEBUG] Could not extract username from album URL.")
        return media

    visited_urls = set()  # To track URLs we've already fetched
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        current_url = album_url
        while current_url:
            if current_url in visited_urls:
                debug_log(f"[DEBUG] Already visited {current_url}, stopping to avoid duplicate fetching.")
                break
            visited_urls.add(current_url)
            content, base_url, status = await get_webpage_content(current_url, session)
            if status != 200:
                debug_log(f"[DEBUG] Failed to fetch page: {current_url} (status {status})")
                break
            soup = BeautifulSoup(content, 'html.parser')
            # Extract media from current page
            page_images = [img['src'] for img in soup.find_all('img', src=True)
                           if img['src'].startswith("https://fapello.com/content/") and f"/{username}/" in img['src']]
            video_tags = soup.find_all('source', type="video/mp4", src=True)
            page_videos = [vid['src'] for vid in video_tags
                           if vid['src'].startswith("https://cdn.fapello.com/content/") and 
                              (vid['src'].endswith(".mp4") or vid['src'].endswith(".m4v"))]
            media["images"].extend(page_images)
            media["videos"].extend(page_videos)
            debug_log(f"[DEBUG] {current_url}: Found {len(page_images)} images and {len(page_videos)} videos")
            # Check for next page marker (infinite scroll)
            next_div = soup.find("div", id="next_page")
            if next_div:
                next_link = next_div.find("a", href=True)
                if next_link:
                    # If the next page URL is relative, build an absolute URL.
                    current_url = urllib.parse.urljoin(base_url, next_link['href'])
                else:
                    break
            else:
                break

        # Remove duplicates from collected media.
        media["images"] = list(set(media["images"]))
        media["videos"] = list(set(media["videos"]))
        debug_log(f"[DEBUG] Total media collected for {username}: {len(media['images'])} images and {len(media['videos'])} videos")
        return media
async def extract_jpg5_album_media_urls(album_url: str) -> list:
    media_urls = set()
    next_page_url = album_url.rstrip('/')

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        while next_page_url:
            debug_log(f"[DEBUG] Fetching JPG5 album page: {next_page_url}")
            try:
                async with session.get(next_page_url) as response:
                    if response.status != 200:
                        debug_log(f"[DEBUG] Page {next_page_url} returned status {response.status}, stopping pagination.")
                        break
                    content = await response.text()
            except Exception as e:
                debug_log(f"[DEBUG] Error fetching {next_page_url}: {e}")
                break

            soup = BeautifulSoup(content, 'html.parser')
            imgs = soup.find_all('img', src=True)
            current_page_media = {img['src'] for img in imgs if "jpg5.su" in img['src']}
            
            if not current_page_media:
                debug_log(f"[DEBUG] No media found on {next_page_url}. Stopping pagination.")
                break
            
            new_media = current_page_media - media_urls
            if not new_media:
                debug_log("[DEBUG] No new media found. Stopping pagination.")
                break

            media_urls.update(new_media)
            debug_log(f"[DEBUG] Added {len(new_media)} new media URLs. Total collected: {len(media_urls)}")

            next_page = soup.find("a", {"data-pagination": "next"})
            if next_page and "href" in next_page.attrs:
                next_page_url = next_page["href"]
                if not next_page_url.startswith("http"):
                    next_page_url = f"https://jpg5.su{next_page_url}"
            else:
                debug_log("[DEBUG] No next page link found. Pagination complete.")
                break

    return list(media_urls)

def list_downloaded_images(folder: str) -> list:
    valid_extensions = [".png", ".jpg", ".jpeg", ".gif"]
    files = []
    if os.path.exists(folder):
        for file in os.listdir(folder):
            if any(file.lower().endswith(ext) for ext in valid_extensions):
                files.append(os.path.join(folder, file))
    return files

def main():
    st.markdown(
        """
        <style>
        body {
            background-color: #121212;
            color: #E0E0E0;
        }
        .title {
            font-size: 36px;
            font-weight: bold;
            text-align: center;
            color: #4CAF50;
            margin-bottom: 20px;
        }
        .subheading {
            font-size: 24px;
            font-weight: bold;
            color: #E0E0E0;
        }
        .instruction {
            font-size: 16px;
            color: #B0B0B0;
        }
        .stTextInput, .stButton, .stText {
            background-color: #1e1e1e;
            color: #ffffff;
        }
        .stTabs [data-baseweb="tab"] {
            color: #f06a00;
        }
        .stTabs [data-baseweb="tab"]:hover {
            color: #ff9800;
        }
        .stImage {
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    logo_url = "https://media.discordapp.net/attachments/1274042631755796481/1355694086190792805/Exiled.gif?ex=67e9dc01&is=67e88a81&hm=d2768ac1c63abd5eb6254ed9a4d21bd2a9d2d9353170bf6239f4b1bcab7abf98&="
    st.image(logo_url, use_container_width=True)
    st.markdown('<p class="instruction">Select a tab to start exploring content.</p>', unsafe_allow_html=True)
    
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Erome Albums",
        "Erome Gallery",
        "Bunkr Albums",
        "Bunkr Gallery",
        "Fapello Gallery",
        "JPG5 Gallery"
    ])
    
    with tab1:
        st.markdown('<h3 class="subheading">Search Erome Albums</h3>', unsafe_allow_html=True)
        query = st.text_input("Enter an Erome username:", placeholder="e.g., username123", key="album_search")
        if query:
            st.write(f"🔍 Searching for albums by: **{query}**")
            try:
                album_links = asyncio.run(fetch_all_album_pages(query))
                if album_links:
                    st.write("### 📚 Albums Found:")
                    for link in album_links:
                        st.markdown(f"- [View Album]({link})")
                else:
                    st.warning("No albums found for the provided username.")
            except Exception as e:
                st.error(f"An error occurred: {e}")

    # Updated Tab 2: Erome Image Gallery (using image URLs directly)
    with tab2:
        st.markdown('<h3 class="subheading">Erome Image Gallery</h3>', unsafe_allow_html=True)
        username = st.text_input("Enter an Erome username to fetch images:", placeholder="e.g., username123", key="image_gallery")
        if username:
            st.write(f"🌐 Fetching images from **{username}**'s albums...")
            try:
                album_links = asyncio.run(fetch_all_album_pages(username))
                if album_links:
                    image_urls = asyncio.run(fetch_all_erome_image_urls(album_links))
                    if image_urls:
                        st.markdown("### 🖼️ Images Found:")
                        cols = st.columns(3)
                        for i, image_url in enumerate(image_urls):
                            with cols[i % 3]:
                                st.image(image_url, use_container_width=True)
                    else:
                        st.warning("No images found in the albums.")
                else:
                    st.warning("No albums found for the provided username.")
            except Exception as e:
                st.error(f"An error occurred: {e}")

    with tab3:
        st.markdown('<h3 class="subheading">Bunkr Links Finder</h3>', unsafe_allow_html=True)
        bunkr_input = st.text_input("Enter a Bunkr username:", placeholder="e.g., username123", key="bunkr_search")
        if bunkr_input:
            st.write(f"🔍 Searching for links by: **{bunkr_input}**")
            try:
                asyncio.set_event_loop(asyncio.new_event_loop())
                asyncio.run(search_bunkr_links(bunkr_input))
            except Exception as e:
                st.error(f"An error occurred: {e}")

    with tab4:
        st.markdown('<h3 class="subheading">Bunkr Image Gallery</h3>', unsafe_allow_html=True)
        bunkr_gallery_input = st.text_input("Enter a Bunkr username for image gallery:", placeholder="e.g., username123", key="bunkr_gallery")
        if bunkr_gallery_input:
            st.write(f"🌐 Fetching Bunkr images for **{bunkr_gallery_input}** ...")
            try:
                image_urls = asyncio.run(fetch_bunkr_gallery_images(bunkr_gallery_input))
                if image_urls:
                    st.markdown("### 🖼️ Images Found:")
                    cols = st.columns(3)
                    for i, img_url in enumerate(image_urls):
                        with cols[i % 3]:
                            st.image(img_url, use_container_width=True)
                else:
                    st.warning("No images found for the provided Bunkr username.")
            except Exception as e:
                st.error(f"An error occurred: {e}")

    with tab5:
        st.markdown('<h3 class="subheading">Fapello Image Gallery</h3>', unsafe_allow_html=True)
        fapello_album_url = st.text_input("Enter a Fapello album URL:", placeholder="e.g., https://fapello.com/emmaxbelle/", key="fapello_album")
        if fapello_album_url:
            st.write(f"🌐 Fetching media from album: **{fapello_album_url}**")
            try:
                media = asyncio.run(fetch_fapello_album_media(fapello_album_url))
                if media["images"] or media["videos"]:
                    if media["images"]:
                        st.markdown("### 🖼️ Images Found:")
                        cols = st.columns(3)
                        for i, image_url in enumerate(media["images"]):
                            with cols[i % 3]:
                                st.image(image_url, use_container_width=True)
                    if media["videos"]:
                        st.markdown("### 🎥 Videos Found:")
                        for video_url in media["videos"]:
                            st.video(video_url)
                else:
                    st.warning("No media found for the provided Fapello album URL.")
            except Exception as e:
                st.error(f"An error occurred: {e}")


    with tab6:
        st.markdown('<h3 class="subheading">JPG5 Image Gallery</h3>', unsafe_allow_html=True)
        jpg5_album_url = st.text_input("Enter a JPG5 album URL:", placeholder="e.g., https://jpg5.su/album/...", key="jpg5_album")
        if jpg5_album_url:
            st.write(f"🌐 Fetching JPG5 images from album: **{jpg5_album_url}**")
            try:
                media_urls = asyncio.run(extract_jpg5_album_media_urls(jpg5_album_url))
                if media_urls:
                    st.markdown("### 🖼️ Images Found:")
                    cols = st.columns(3)
                    for i, media_url in enumerate(media_urls):
                        with cols[i % 3]:
                            st.image(media_url, use_container_width=True)
                else:
                    st.warning("No images found for the provided JPG5 album URL.")
            except Exception as e:
                st.error(f"An error occurred: {e}")

    st.sidebar.title("Made By Cass")
    st.sidebar.subheader("About This Site")
    st.sidebar.markdown("""
This website is a tool designed to help you explore and download media content from platforms like **Erome**, **Bunkr**, **Fapello**, and **JPG5**.

### How It Works:
1. **Search Content**: Enter a username or URL into the search bar to retrieve albums, images, or links.
2. **View and Download**: Browse the results and download media directly or view them in a grid layout.
3. **Easy Navigation**: Use the tabs to switch between different content sources seamlessly.

Stay tuned for additional features and more supported platforms!
""")

if __name__ == "__main__":
    main()
