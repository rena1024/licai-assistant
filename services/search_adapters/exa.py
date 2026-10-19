"""Exa fast；不启用正文、摘要或深度增强。"""
from services.external_http import post_json


def search(key, query):
    data = post_json('https://api.exa.ai/search', headers={'x-api-key':key},
                    payload={'query':query,'type':'fast','numResults':5,'contents':{'text':False}})
    return [{'title':str(r.get('title',''))[:300], 'url':str(r.get('url',''))[:2000],
             'snippet':'', 'published_at':r.get('publishedDate'), 'full_text':False}
            for r in data.get('results',[])[:5]]
