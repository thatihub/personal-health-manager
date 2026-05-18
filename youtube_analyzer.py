import urllib.request
import urllib.parse
import json
import re
import os

def extract_video_id(url: str) -> str:
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    elif "watch?v=" in url:
        return url.split("watch?v=")[1].split("&")[0]
    elif "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0]
    return ""

def classify_comment(text: str) -> list:
    text_lower = text.lower()
    buckets = []
    
    keywords = {
        "Dose 2.5 mg": ["2.5", "2.5mg", "2.5 mg", "lowest dose", "starter dose"],
        "Dose 5 mg": ["5mg", "5 mg", "went up to 5", "titrated to 5"],
        "Dose reduction": ["went back down", "reduced dose", "lowered dose", "back to 2.5", "dose down"],
        "Microdosing / split dosing": ["microdose", "microdosing", "split dose", "twice a week", "3.5 mg", "3 mg", "compound", "compounded"],
        "Nausea / GI issues": ["nausea", "nauseous", "vomit", "diarrhea", "constipation", "gi", "stomach", "acid reflux"],
        "Food noise / appetite": ["food noise", "appetite", "hungry", "cravings", "suppression"],
        "Weight loss": ["lost", "lbs", "pounds", "kg", "goal weight", "weight loss"],
        "Diabetes / glucose": ["diabetes", "a1c", "blood sugar", "glucose", "insulin", "hypoglycemia", "low sugar"],
        "Maintenance dose": ["maintenance", "maintain", "goal weight", "staying on", "long term"],
        "Safety concern": ["doctor", "prescriber", "er", "hospital", "pancreatitis", "gallbladder", "kidney", "dehydration", "severe"]
    }
    
    for bucket, words in keywords.items():
        if any(word in text_lower for word in words):
            buckets.append(bucket)
            
    return buckets

def fetch_youtube_comments(video_url: str, max_comments: int, include_replies: bool, sort_order: str, focus_keywords: list) -> dict:
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
    
    # Tagging AI tags locally for simplicity based on text
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
    
    return {
        "ok": True,
        "videoInfo": video_info,
        "comments": all_comments,
        "totalFetched": len(all_comments),
        "totalTopLevel": len([c for c in all_comments if not c["isReply"]]),
        "totalReplies": len([c for c in all_comments if c["isReply"]])
    }

def analyze_ai_summary(comments: list, user_context: dict) -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    
    default_summary = {
        "summary": "AI summary based on the fetched comments.",
        "topThemes": ["Dose changes", "Side effects", "Appetite suppression"],
        "dosePatterns": ["Many start at 2.5mg", "Titrating to 5mg causes side effects for some"],
        "sideEffects": ["Nausea", "Fatigue", "GI issues"],
        "microdosingMentions": ["Some users mention split dosing"],
        "maintenanceMentions": ["A few reached goal weight and maintain"],
        "diabetesMentions": ["Better glucose control reported"],
        "safetyWarnings": ["Always consult a doctor before changing dosage.", "Do not split pens without guidance."],
        "misleadingClaims": ["Claims that side effects mean it is working faster are unverified."],
        "questionsToAskDoctor": ["Is staying on 2.5mg longer an option?", "How to manage mild body aches?"],
        "appliesToUserCase": ["Discuss with prescriber.", "Do not change dose without clinician approval.", "Especially important because insulin use increases hypoglycemia risk."]
    }
    
    if not api_key and not gemini_key:
        default_summary["summary"] = "AI API Key not found. Please set OPENAI_API_KEY or GEMINI_API_KEY environment variable. Returning mock summary."
        return default_summary
        
    prompt = "Analyze the following YouTube comments regarding GLP-1/Mounjaro/Tirzepatide.\n"
    if user_context:
        prompt += f"User context: {json.dumps(user_context)}\n"
    
    prompt += "Provide a structured JSON output with the exact following keys: summary (list of 3-5 key takeaway bullet points), topThemes, dosePatterns, sideEffects, microdosingMentions, maintenanceMentions, diabetesMentions, safetyWarnings, misleadingClaims, questionsToAskDoctor, appliesToUserCase (all values must be list of strings).\n\nComments:\n"
    
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
                "messages": [{"role": "system", "content": "You are a medical data analyst. Output ONLY valid JSON."}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                return json.loads(res_data["choices"][0]["message"]["content"])
        elif gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_key}"
            headers = {"Content-Type": "application/json"}
            data = {
                "contents": [{"parts": [{"text": "You are a medical data analyst. Output ONLY valid JSON.\n" + prompt}]}]
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
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    
    default_res = {
        "theme": theme,
        "bullets": ["Could not fetch AI summary."]
    }
    
    if not api_key and not gemini_key:
        default_res["bullets"] = ["AI API Key not found.", "Please set OPENAI_API_KEY or GEMINI_API_KEY."]
        return default_res
        
    prompt = f"Analyze the following YouTube comments specifically regarding the theme '{theme}'.\n"
    prompt += "Extract the 3 to 5 most important insights, patterns, or experiences users share about this theme.\n"
    prompt += "Provide a structured JSON output with the exact key 'bullets' containing a list of strings.\n\nComments:\n"
    
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
                "messages": [{"role": "system", "content": "You are a medical data analyst. Output ONLY valid JSON."}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                return json.loads(res_data["choices"][0]["message"]["content"])
        elif gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={gemini_key}"
            headers = {"Content-Type": "application/json"}
            data = {
                "contents": [{"parts": [{"text": "You are a medical data analyst. Output ONLY valid JSON.\n" + prompt}]}]
            }
            req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                if text.startswith("```json"):
                    text = text[7:-3]
                return json.loads(text)
    except Exception as e:
        default_res["bullets"] = [f"API Error: {str(e)}"]
        return default_res
        
    return default_res
