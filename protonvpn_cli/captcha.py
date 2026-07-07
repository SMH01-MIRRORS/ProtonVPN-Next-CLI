import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import threading
import json
import webbrowser
import socket
import time

class CaptchaProxyServer:
    def __init__(self, proxy_base, session_id):
        self.proxy_base = proxy_base.rstrip('/')
        self.session_id = session_id
        self.token = None
        self.server = None

    def start_and_wait(self, original_web_url):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()

        handler_class = self.create_handler_class()
        
        # We need a custom server that can be shut down from within a request handler
        class StoppableServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            pass

        self.server = StoppableServer(('127.0.0.1', port), handler_class)
        
        local_url = f"http://127.0.0.1:{port}/?url=" + urllib.parse.quote(original_web_url)
        print(f"\n[CAPTCHA] Proton requires Human Verification.")
        print(f"[CAPTCHA] Opening your default browser...")
        print(f"[CAPTCHA] If the browser does not open, manually navigate to:\n{local_url}\n")
        
        # Open the browser in a separate thread so we can start serving immediately
        threading.Thread(target=lambda: webbrowser.open(local_url), daemon=True).start()
        
        # Serve until the token is received and server is shut down
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.server.server_close()
            
        return self.token

    def create_handler_class(self):
        parent = self
        class ProxyHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            
            def do_POST(self):
                if self.path == '/submit_token':
                    content_length = int(self.headers.get('Content-Length', 0))
                    token = self.rfile.read(content_length).decode('utf-8')
                    parent.token = token
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK")
                    # Shutdown the server in a separate thread to allow the response to finish
                    threading.Thread(target=parent.server.shutdown, daemon=True).start()
                    return
                self.proxy_request('POST')

            def do_GET(self):
                self.proxy_request('GET')

            def proxy_request(self, method):
                path = self.path
                if path.startswith('/?url='):
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
                    orig_url = qs.get('url', [''])[0]
                    parsed = urllib.parse.urlparse(orig_url)
                    
                    path = parsed.path
                    if not path.startswith('/verify'):
                        path = '/verify' + path
                        
                    if parsed.query:
                        path += '?' + parsed.query
                        if 'embed=true' not in path:
                            path += '&embed=true&theme=1&vpn=true'
                    else:
                        path += '?embed=true&theme=1&vpn=true'

                target_url = parent.proxy_base + path
                req = urllib.request.Request(target_url, method=method)
                
                # Copy headers except those that cause issues
                for k, v in self.headers.items():
                    if k.lower() not in ['host', 'accept-encoding', 'connection']:
                        req.add_header(k, v)
                
                req.add_header('x-pm-appversion', 'android-vpn@1.0.0-dev+play')
                req.add_header('x-pm-apiversion', '4')
                if parent.session_id:
                    req.add_header('x-pm-uid', parent.session_id)
                
                if method == 'POST':
                    content_length = int(self.headers.get('Content-Length', 0))
                    if content_length > 0:
                        req.data = self.rfile.read(content_length)

                try:
                    res = urllib.request.urlopen(req)
                    body = res.read()
                    headers = res.headers
                    status = res.status
                except urllib.error.HTTPError as e:
                    body = e.read()
                    headers = e.headers
                    status = e.code
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(str(e).encode('utf-8'))
                    return

                self.send_response(status)
                
                is_html = False
                for k, v in headers.items():
                    k_lower = k.lower()
                    if k_lower in ['content-security-policy', 'x-frame-options', 'transfer-encoding', 'strict-transport-security']:
                        continue
                    if k_lower == 'content-type' and 'text/html' in v.lower():
                        is_html = True
                    self.send_header(k, v)
                
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                if is_html:
                    html = body.decode('utf-8', 'ignore')
                    js_inject = f"""
                    <script>
                    (function() {{
                        var proxyBase = '{parent.proxy_base}';
                        function rewriteUrl(url) {{
                            if (typeof url !== 'string') return url;
                            if (url.startsWith('https://verify-api.proton.me')) {{
                                return url.replace('https://verify-api.proton.me', '/verify-api');
                            }}
                            if (url.startsWith('https://verify.proton.me')) {{
                                return url.replace('https://verify.proton.me', '/verify');
                            }}
                            if (url.startsWith(proxyBase)) {{
                                return url.replace(proxyBase, '');
                            }}
                            return url;
                        }}
                        var origFetch = window.fetch;
                        window.fetch = function() {{
                            if (arguments[0] instanceof Request) {{
                                var newUrl = rewriteUrl(arguments[0].url);
                                if (newUrl !== arguments[0].url) {{
                                    arguments[0] = new Request(newUrl, arguments[0]);
                                }}
                            }} else {{
                                arguments[0] = rewriteUrl(arguments[0]);
                            }}
                            return origFetch.apply(this, arguments);
                        }};
                        var origOpen = XMLHttpRequest.prototype.open;
                        XMLHttpRequest.prototype.open = function() {{
                            arguments[1] = rewriteUrl(arguments[1]);
                            return origOpen.apply(this, arguments);
                        }};
                        
                        window.addEventListener('message', function(event) {{
                            var data = event.data;
                            if (typeof data === 'string') {{
                                try {{ data = JSON.parse(data); }} catch(e){{}}
                            }}
                            if (data && (data.type === 'HUMAN_VERIFICATION_SUCCESS' || data.type === 'Success')) {{
                                var token = data.payload ? data.payload.token : data.token;
                                fetch('/submit_token', {{method: 'POST', body: token}}).then(() => {{
                                    document.body.innerHTML = '<h2 style="color:white;text-align:center;margin-top:50px;font-family:sans-serif;">Success! You can close this tab and return to the terminal.</h2>';
                                }});
                            }}
                        }});
                    }})();
                    </script>
                    """
                    if '<head>' in html:
                        html = html.replace('<head>', '<head>' + js_inject, 1)
                    else:
                        html = js_inject + html
                    self.wfile.write(html.encode('utf-8'))
                else:
                    self.wfile.write(body)

        return ProxyHandler
