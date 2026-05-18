import urllib.request
import urllib.parse
import json
import re
import os
from youtube_transcript_api import YouTubeTranscriptApi

def extract_video_id(url: str) -> str:
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    elif "watch?v=" in url:
        return url.split("watch?v=")[1].split("&")[0]
    elif "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0]
    return ""

def classify_comment(text: str) -> list:
    # Deprecated: The AI now handles all dynamic theme classification.
    return []

def fetch_youtube_comments(video_url: str, max_comments: int, include_replies: bool, sort_order: str, focus_keywords: list, analysis_mode: str = "comments_only") -> dict:
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return {"error": "YOUTUBE_API_KEY environment variable is not set."}
        
    video_id = extract_video_id(video_url)
    if not video_id:
        return {"error": "Invalid YouTube URL."}
        
    order = "relevance" if sort_order == "relevance" else "time"
    
    all_comments = []
    page_token = ""
    
    video_info = {"title": "Unknown Video", "channelTitle": "Unknown Channel"}
    try:
        vid_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={video_id}&key={api_key}"
        req = urllib.request.Request(vid_url)
        with urllib.request.urlopen(req) as response:
            vid_data = json.loads(response.read().decode('utf-8'))
            if vid_data.get("items"):
                video_info["title"] = vid_data["items"][0]["snippet"]["title"]
                video_info["channelTitle"] = vid_data["items"][0]["snippet"]["channelTitle"]
    except Exception:
        pass

    try:
        while len(all_comments) < max_comments:
            url = f"https://www.googleapis.com/youtube/v3/commentThreads?part=snippet,replies&videoId={video_id}&key={api_key}&maxResults=100&order={order}"
            if page_token:
                url += f"&pageToken={page_token}"
                
            req = urllib.request.Request(url)
            try:
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    return {"error": "Comments disabled or API quota exceeded."}
                elif e.code == 404:
                    return {"error": "Video not found."}
                else:
                    return {"error": f"YouTube API Error: {e.reason}"}
                    
            for item in data.get("items", []):
                top_comment = item["snippet"]["topLevelComment"]["snippet"]
                comment_id = item["id"]
                total_reply_count = item["snippet"]["totalReplyCount"]
                
                c_text = top_comment["textOriginal"]
                buckets = classify_comment(c_text)
                
                all_comments.append({
                    "id": comment_id,
                    "parentId": None,
                    "isReply": False,
                    "author": top_comment["authorDisplayName"],
                    "text": c_text,
                    "likeCount": top_comment.get("likeCount", 0),
                    "publishedAt": top_comment["publishedAt"],
                    "updatedAt": top_comment["updatedAt"],
                    "matchedBuckets": buckets,
                    "aiTags": []
                })
                
                if include_replies and total_reply_count > 0:
                    replies_data = item.get("replies", {}).get("comments", [])
                    if len(replies_data) == total_reply_count:
                        for rep in replies_data:
                            r_text = rep["snippet"]["textOriginal"]
                            all_comments.append({
                                "id": rep["id"],
                                "parentId": comment_id,
                                "isReply": True,
                                "author": rep["snippet"]["authorDisplayName"],
                                "text": r_text,
                                "likeCount": rep["snippet"].get("likeCount", 0),
                                "publishedAt": rep["snippet"]["publishedAt"],
                                "updatedAt": rep["snippet"]["updatedAt"],
                                "matchedBuckets": classify_comment(r_text),
                                "aiTags": []
                            })
                    else:
                        rep_url = f"https://www.googleapis.com/youtube/v3/comments?part=snippet&parentId={comment_id}&key={api_key}&maxResults=100"
                        try:
                            with urllib.request.urlopen(urllib.request.Request(rep_url)) as r_res:
                                r_data = json.loads(r_res.read().decode('utf-8'))
                                for rep in r_data.get("items", []):
                                    r_text = rep["snippet"]["textOriginal"]
                                    all_comments.append({
                                        "id": rep["id"],
                                        "parentId": comment_id,
                                        "isReply": True,
                                        "author": rep["snippet"]["authorDisplayName"],
                                        "text": r_text,
                                        "likeCount": rep["snippet"].get("likeCount", 0),
                                        "publishedAt": rep["snippet"]["publishedAt"],
                                        "updatedAt": rep["snippet"]["updatedAt"],
                                        "matchedBuckets": classify_comment(r_text),
                                        "aiTags": []
                                    })
                        except Exception:
                            pass
                            
                if len(all_comments) >= max_comments:
                    break
                    
            page_token = data.get("nextPageToken")
            if not page_token:
                break
                
    except Exception as e:
        return {"error": f"Network/API failure: {str(e)}"}
        
    all_comments = all_comments[:max_comments]
    
    for c in all_comments:
        tags = []
        lower_t = c["text"].lower()
        if any(w in lower_t for w in ["doctor", "prescriber", "my endo", "medical"]):
            tags.append("Needs doctor confirmation")
        if any(w in lower_t for w in ["split the pen", "microdose", "compounded"]):
            tags.append("Potentially unsafe claim")
        if any(w in lower_t for w in ["works great", "helped me", "lost", "side effect"]):
            tags.append("Anecdote")
        if not tags:
            tags.append("Possible useful pattern")
        c["aiTags"] = tags
    
    result = {
        "ok": True,
        "videoInfo": video_info,
        "totalFetched": len(all_comments),
        "totalReplies": sum(1 for c in all_comments if c["isReply"]),
        "comments": all_comments,
        "transcript": ""
    }
    
    if analysis_mode in ["video_only", "both"]:
        try:
            api = YouTubeTranscriptApi()
            transcript_obj = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
            transcript_text = " ".join([snippet.text for snippet in transcript_obj.snippets])
            result["transcript"] = transcript_text
        except Exception as e:
            print("Failed to fetch transcript:", str(e))
            result["transcript"] = ""

    return result

def analyze_ai_summary(comments: list, user_context: dict, transcript: str = "") -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    
    default_summary = {
        "summary": ["AI summary based on the fetched content."],
        "dynamicThemes": [
            {
                "themeName": "General Discussion",
                "themeDescription": "Common points raised in the comments.",
                "icon": "💬",
                "representativeComments": ["Could not load specific comments."]
            }
        ]
    }
    
    if not api_key and not gemini_key:
        default_summary["summary"] = ["AI API Key not found. Please set OPENAI_API_KEY or GEMINI_API_KEY."]
        return default_summary
        
    prompt = ""
    mode = user_context.get("mode", "comments_only") if user_context else "comments_only"
    
    if mode == "video_only":
        prompt += "Analyze the following YouTube video transcript.\n"
    elif mode == "both":
        prompt += "Analyze the following YouTube video transcript AND user comments. Pay attention to what the video claims vs what the audience experiences.\n"
    else:
        prompt += "Analyze the following YouTube user comments.\n"
        
    if user_context and "text" in user_context and user_context["text"]:
        prompt += f"User specific context or question: {user_context['text']}\n"
    
    prompt += "Provide a structured JSON output with the exact following keys:\n"
    prompt += "1. 'summary': list of strings (up to 10 key takeaway bullet points summarizing the content).\n"
    prompt += "2. 'dynamicThemes': list of objects representing the top 4 to 6 themes found in the content. Each object must have: 'themeName' (string, short title), 'themeDescription' (string, 1-2 sentences explaining the theme), 'icon' (string, a single emoji representing the theme), and 'representativeComments' (list of strings, 3-5 actual or paraphrased quotes showing this theme).\n\n"
    
    if mode in ["video_only", "both"]:
        if transcript:
            # Limit transcript length to roughly 10000 chars to save tokens
            prompt += f"Video Transcript:\n{transcript[:10000]}\n\n"
        else:
            prompt += "Video Transcript: [No transcript available for this video.]\nPlease note: Return an error message in the summary that no transcript was found, instead of hallucinating content.\n\n"
        
    if comments and mode in ["comments_only", "both"]:
        prompt += "Comments:\n"
        sample = [c["text"] for c in comments[:30]]
        prompt += "\n".join(sample)
    
    try:
        if api_key:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
            data = {
                "model": "gpt-4o-mini",
                "messages": [{"role": "system", "content": "You are an expert data analyst. Output ONLY valid JSON."}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                return json.loads(res_data["choices"][0]["message"]["content"])
        elif gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
            headers = {"Content-Type": "application/json"}
            data = {
                "contents": [{"parts": [{"text": "You are an expert data analyst. Output ONLY valid JSON.\n" + prompt}]}]
            }
            req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                if text.startswith("```json"):
                    text = text[7:-3]
                return json.loads(text)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        default_summary["summary"] = [f"AI API Error: {str(e)}", f"Details: {error_body}"]
        return default_summary
    except Exception as e:
        default_summary["summary"] = [f"AI API Error: {str(e)}", "Returning default."]
        return default_summary
    
    return default_summary

def analyze_theme_summary(comments: list, theme: str) -> dict:
    return {"theme": theme, "bullets": []}
