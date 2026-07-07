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
        from .database import Database
        bypass = Database().get_setting("api_bypass", "0")
        if bypass in ("1", "cloudflare"):
            self.proxy_base = "https://api.protonnext.qzz.io"
        elif bypass in ("2", "netlify"):
            self.proxy_base = "https://shimmering-stroopwafel-51675e.netlify.app"
        elif bypass in ("3", "deno"):
            self.proxy_base = "https://quick-bluejay-8760.smh01-mirrors.deno.net"
        else:
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
        
        parsed = urllib.parse.urlparse(original_web_url)
        proxy_path = f"/verify/{parsed.path.lstrip('/')}"
        if parsed.query:
            proxy_path += f"?{parsed.query}"
        
        local_url = f"http://127.0.0.1:{port}{proxy_path}"
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
                elif path == '/submit_token':
                    pass
                elif path.startswith('/captcha/'):
                    path = '/verify-api' + path
                elif not path.startswith('/verify-api') and not path.startswith('/verify'):
                    path = '/verify' + path

                target_url = parent.proxy_base + path
                req = urllib.request.Request(target_url, method=method)
                
                # Copy headers except those that cause issues
                for k, v in self.headers.items():
                    k_lower = k.lower()
                    if k_lower not in ['host', 'accept-encoding', 'connection', 'user-agent']:
                        if k_lower == 'referer' or k_lower == 'origin':
                            v = 'https://verify.proton.me'
                        req.add_header(k, v)

                from .device_info import DeviceInfoProvider
                req.add_header('User-Agent', DeviceInfoProvider().get_spoofed_user_agent())
                req.add_header('x-pm-appversion', f'android-vpn@{DeviceInfoProvider.SPOOFED_APP_VERSION}-dev+play')
                req.add_header('x-pm-apiversion', '4')
                if parent.session_id:
                    req.add_header('x-pm-uid', parent.session_id)


                
                if method == 'POST':
                    content_length = int(self.headers.get('Content-Length', 0))
                    if content_length > 0:
                        req.data = self.rfile.read(content_length)

                class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        return None
                
                opener = urllib.request.build_opener(NoRedirectHandler)

                try:
                    res = opener.open(req)
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
                        
                    if k_lower == 'location':
                        if v.startswith(parent.proxy_base):
                            v = v.replace(parent.proxy_base, '')
                        elif v.startswith('https://verify.proton.me'):
                            v = v.replace('https://verify.proton.me', '/verify')
                        elif v.startswith('https://verify-api.proton.me'):
                            v = v.replace('https://verify-api.proton.me', '/verify-api')
                            
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
