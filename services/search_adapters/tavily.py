"""Tavily basic；关闭自动增强、答案、正文提取。"""
from services.external_http import post_json


def search(key, query):
    data = post_json('https://api.tavily.com/search', headers={'Authorization':'Bearer '+key},
        payload={'query':query, 'search_depth':'basic', 'auto_parameters':False,
                 'max_results':5, 'include_answer':False, 'include_raw_content':False,
                 'include_images':False, 'include_usage':True})
    return [{'title':str(r.get('title',''))[:300], 'url':str(r.get('url',''))[:2000],
             'snippet':str(r.get('content',''))[:2000], 'published_at':r.get('published_date'),
             'full_text':False} for r in data.get('results',[])[:5]]
