package es.upv.idf.sparkhub;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Spark AI Hub for Android: a mobile shell around the hub web app.
 *
 * The hub is only reachable inside the Tailscale network. On start the app checks whether the hub answers; if not, it
 * shows a native screen that explains the situation, tells whether a VPN (Tailscale) is active and opens the official
 * Tailscale app (or Play Store to install it) so the user can sign in. When the user comes back, the check runs again.
 * Android allows a single active VPN, so the official Tailscale app is used instead of embedding a second VPN client.
 *
 * Inside the web view: login with the hub account (the web adapts menus to admin / manager / researcher), file and
 * camera pickers for chat attachments, downloads, microphone for audio, and "Share to Spark AI Hub" from other apps
 * (files are uploaded to the user's drive and opened in a new chat).
 */
public class MainActivity extends Activity {
    private static final String DEFAULT_URL = "__SAH_PUBLIC_BASE_URL__";  // filled in by build.sh from .env
    private static final String TAILSCALE_PKG = "com.tailscale.ipn";
    private static final int REQ_FILES = 41;
    private static final int REQ_PERMS = 42;

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private WebView web;
    private LinearLayout connectView;
    private TextView statusText;
    private ProgressBar spinner;
    private ValueCallback<Uri[]> fileCallback;
    private Uri cameraUri;
    private List<Uri> pendingShare = new ArrayList<>();
    private boolean webLoaded = false;
    private boolean checking = false;

    private boolean es() {
        return !"en".equals(Locale.getDefault().getLanguage());
    }

    private String L(String es, String en) {
        return es() ? es : en;
    }

    private SharedPreferences prefs() {
        return getSharedPreferences("hub", MODE_PRIVATE);
    }

    private String baseUrl() {
        return prefs().getString("url", DEFAULT_URL);
    }

    // ------------------------------------------------------------------ lifecycle

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(15, 118, 110));
        buildConnectView();
        setContentView(connectView);
        collectShare(getIntent());
        if (checkSelfPermission(android.Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{android.Manifest.permission.CAMERA}, REQ_PERMS);
        }
        checkAndOpen();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        collectShare(intent);
        if (webLoaded && !pendingShare.isEmpty()) uploadShared();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!webLoaded) checkAndOpen(); // e.g. back from the Tailscale app after signing in
    }

    @Override
    public void onBackPressed() {
        if (web != null && webLoaded && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    // ------------------------------------------------------------------ connection screen

    private Button button(String text, boolean primary, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(16);
        b.setTextColor(primary ? Color.WHITE : Color.rgb(15, 118, 110));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(28);
        bg.setColor(primary ? Color.rgb(15, 118, 110) : Color.TRANSPARENT);
        bg.setStroke(3, Color.rgb(15, 118, 110));
        b.setBackground(bg);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 140);
        lp.topMargin = 28;
        b.setLayoutParams(lp);
        return b;
    }

    private void buildConnectView() {
        connectView = new LinearLayout(this);
        connectView.setOrientation(LinearLayout.VERTICAL);
        connectView.setPadding(64, 120, 64, 64);
        connectView.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView logo = new TextView(this);
        logo.setText("◆");
        logo.setTextSize(48);
        logo.setTextColor(Color.rgb(15, 118, 110));
        logo.setGravity(Gravity.CENTER);
        connectView.addView(logo);

        TextView title = new TextView(this);
        title.setText("Spark AI Hub");
        title.setTextSize(26);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        connectView.addView(title);

        TextView sub = new TextView(this);
        sub.setText(L("Tu IA privada en el DGX Spark", "Your private AI on the DGX Spark"));
        sub.setGravity(Gravity.CENTER);
        sub.setAlpha(0.7f);
        connectView.addView(sub);

        spinner = new ProgressBar(this);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sp.topMargin = 60;
        spinner.setLayoutParams(sp);
        connectView.addView(spinner);

        statusText = new TextView(this);
        statusText.setTextSize(15);
        statusText.setGravity(Gravity.CENTER);
        statusText.setPadding(0, 40, 0, 20);
        ScrollView sv = new ScrollView(this);
        sv.addView(statusText);
        connectView.addView(sv, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        connectView.addView(button(L("Abrir Tailscale para conectarme", "Open Tailscale to connect"), true, v -> openTailscale()));
        connectView.addView(button(L("Reintentar", "Retry"), false, v -> checkAndOpen()));
        connectView.addView(button(L("Cambiar dirección del servidor", "Change server address"), false, v -> editUrl()));
    }

    private boolean vpnActive() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return false;
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities c = cm.getNetworkCapabilities(n);
            if (c != null && c.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) return true;
        }
        return false;
    }

    private boolean tailscaleInstalled() {
        try {
            getPackageManager().getPackageInfo(TAILSCALE_PKG, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    private void openTailscale() {
        Intent launch = getPackageManager().getLaunchIntentForPackage(TAILSCALE_PKG);
        if (launch != null) {
            startActivity(launch);
            Toast.makeText(this, L("Inicia sesión en Tailscale y pulsa «Connect». Luego vuelve a esta app.",
                    "Sign in to Tailscale and press “Connect”. Then come back to this app."), Toast.LENGTH_LONG).show();
            return;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=" + TAILSCALE_PKG)));
        } catch (ActivityNotFoundException e) {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=" + TAILSCALE_PKG)));
        }
    }

    private void editUrl() {
        EditText et = new EditText(this);
        et.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        et.setText(baseUrl());
        new AlertDialog.Builder(this)
                .setTitle(L("Dirección del Spark AI Hub", "Spark AI Hub address"))
                .setView(et)
                .setPositiveButton("OK", (d, w) -> {
                    String u = et.getText().toString().trim().replaceAll("/+$", "");
                    if (!u.startsWith("https://")) u = "https://" + u;
                    prefs().edit().putString("url", u).apply();
                    checkAndOpen();
                })
                .setNeutralButton(L("Por defecto", "Default"), (d, w) -> {
                    prefs().edit().remove("url").apply();
                    checkAndOpen();
                })
                .setNegativeButton(L("Cancelar", "Cancel"), null)
                .show();
    }

    private void checkAndOpen() {
        if (checking || webLoaded) return;
        checking = true;
        spinner.setVisibility(View.VISIBLE);
        statusText.setText(L("Conectando con el Spark…", "Connecting to the Spark…"));
        final String url = baseUrl();
        io.execute(() -> {
            boolean ok = false;
            String err = "";
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url + "/healthz").openConnection();
                c.setConnectTimeout(6000);
                c.setReadTimeout(6000);
                ok = c.getResponseCode() == 200;
                c.disconnect();
            } catch (Exception e) {
                err = e.getClass().getSimpleName();
            }
            final boolean reachable = ok;
            final String error = err;
            ui.post(() -> {
                checking = false;
                if (reachable) {
                    openWeb();
                } else {
                    spinner.setVisibility(View.GONE);
                    showOffline(error);
                }
            });
        });
    }

    private void showOffline(String error) {
        StringBuilder sb = new StringBuilder();
        boolean installed = tailscaleInstalled();
        boolean vpn = vpnActive();
        sb.append(L("No se puede llegar al Spark AI Hub.\n\n", "The Spark AI Hub cannot be reached.\n\n"));
        sb.append(installed ? L("✔ Tailscale está instalado\n", "✔ Tailscale is installed\n")
                : L("✘ Tailscale no está instalado: pulsa el botón para instalarlo desde Play Store.\n", "✘ Tailscale is not installed: press the button to install it from Play Store.\n"));
        sb.append(vpn ? L("✔ Hay una VPN activa\n", "✔ A VPN is active\n")
                : L("✘ Tailscale no está conectado: ábrelo, inicia sesión y pulsa «Connect».\n", "✘ Tailscale is not connected: open it, sign in and press “Connect”.\n"));
        if (installed && vpn) {
            sb.append(L("\nTailscale está conectado pero el Spark no responde. Comprueba que aceptaste la invitación al equipo «spark» (enlace del correo de invitación) y que usas la misma cuenta de Tailscale.\n",
                    "\nTailscale is connected but the Spark does not answer. Check that you accepted the invitation to the «spark» machine (link in the invitation e-mail) and that you use the same Tailscale account.\n"));
        }
        sb.append("\n").append(baseUrl());
        if (!error.isEmpty()) sb.append("\n(").append(error).append(")");
        statusText.setText(sb.toString());
    }

    // ------------------------------------------------------------------ web view

    private void openWeb() {
        if (web == null) createWebView();
        webLoaded = true;
        setContentView(web);
        String start = baseUrl() + "/?app=android";
        if (!pendingShare.isEmpty()) start = baseUrl() + "/playground?app=android";
        web.loadUrl(start);
        if (!pendingShare.isEmpty()) ui.postDelayed(this::uploadShared, 2500);
    }

    private void createWebView() {
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);
        s.setUserAgentString(s.getUserAgentString() + " SparkAIHubAndroid/0.2.0");
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        final String host = Uri.parse(baseUrl()).getHost();

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (host != null && host.equals(u.getHost())) return false;
                try { // external links (Tailscale download, docs…) open in the browser
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, android.webkit.WebResourceError e) {
                if (req.isForMainFrame()) { // connection lost (Tailscale disconnected): back to the native screen
                    webLoaded = false;
                    setContentView(connectView);
                    spinner.setVisibility(View.GONE);
                    showOffline(String.valueOf(e.getDescription()));
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                Intent chooser = Intent.createChooser(pick, L("Adjuntar", "Attach"));
                if (checkSelfPermission(android.Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                    try {
                        File dir = new File(getCacheDir(), "camera");
                        dir.mkdirs();
                        File photo = new File(dir, "foto-" + System.currentTimeMillis() + ".jpg");
                        cameraUri = CameraFiles.uriFor(MainActivity.this, photo);
                        Intent cam = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
                        cam.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
                        cam.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                        chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{cam});
                    } catch (Exception e) {
                        cameraUri = null;
                    }
                }
                try {
                    startActivityForResult(chooser, REQ_FILES);
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                Uri origin = request.getOrigin();
                if (origin != null && host != null && host.equals(origin.getHost())) {
                    if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                        requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO}, REQ_PERMS);
                    }
                    request.grant(request.getResources());
                } else {
                    request.deny();
                }
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> {
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                r.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
                r.addRequestHeader("User-Agent", userAgent);
                String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
                r.setTitle(name);
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(this, L("Descargando ", "Downloading ") + name, Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            }
        });
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != REQ_FILES || fileCallback == null) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        Uri[] result = null;
        if (resultCode == RESULT_OK) {
            if (data != null && data.getClipData() != null) {
                ClipData cd = data.getClipData();
                result = new Uri[cd.getItemCount()];
                for (int i = 0; i < cd.getItemCount(); i++) result[i] = cd.getItemAt(i).getUri();
            } else if (data != null && data.getData() != null) {
                result = new Uri[]{data.getData()};
            } else if (cameraUri != null) {
                result = new Uri[]{cameraUri};
            }
        }
        fileCallback.onReceiveValue(result);
        fileCallback = null;
    }

    // ------------------------------------------------------------------ share from other apps

    private void collectShare(Intent intent) {
        if (intent == null) return;
        String a = intent.getAction();
        if (Intent.ACTION_SEND.equals(a)) {
            Uri u = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (u != null) pendingShare.add(u);
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(a)) {
            ArrayList<Uri> us = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (us != null) pendingShare.addAll(us);
        }
    }

    private String displayName(Uri u) {
        try (Cursor c = getContentResolver().query(u, null, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (i >= 0) return c.getString(i);
            }
        } catch (Exception ignored) {
        }
        String last = u.getLastPathSegment();
        return last != null ? last : "archivo";
    }

    private static String cookieValue(String cookies, String name) {
        if (cookies == null) return "";
        for (String part : cookies.split(";")) {
            String p = part.trim();
            if (p.startsWith(name + "=")) return p.substring(name.length() + 1);
        }
        return "";
    }

    /** Upload shared files to the user's drive (folder "Android") with the web session, then open a chat with them. */
    private void uploadShared() {
        final List<Uri> uris = new ArrayList<>(pendingShare);
        pendingShare.clear();
        final String base = baseUrl();
        final String cookies = CookieManager.getInstance().getCookie(base);
        if (cookieValue(cookies, "sah_session").isEmpty()) {
            Toast.makeText(this, L("Inicia sesión y vuelve a compartir el archivo.", "Sign in and share the file again."), Toast.LENGTH_LONG).show();
            return;
        }
        Toast.makeText(this, L("Subiendo ", "Uploading ") + uris.size() + L(" archivo(s)…", " file(s)…"), Toast.LENGTH_SHORT).show();
        io.execute(() -> {
            List<String> ids = new ArrayList<>();
            String error = null;
            for (Uri u : uris) {
                try {
                    ids.add(upload(base, cookies, u));
                } catch (Exception e) {
                    error = e.getMessage();
                }
            }
            final String err = error;
            ui.post(() -> {
                if (!ids.isEmpty()) web.loadUrl(base + "/playground?app=android&attach=" + String.join(",", ids));
                if (err != null) Toast.makeText(this, err, Toast.LENGTH_LONG).show();
            });
        });
    }

    private String upload(String base, String cookies, Uri u) throws Exception {
        String boundary = "----spark" + UUID.randomUUID();
        HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/v1/files").openConnection();
        c.setDoOutput(true);
        c.setRequestMethod("POST");
        c.setRequestProperty("Cookie", cookies);
        c.setRequestProperty("X-CSRF-Token", cookieValue(cookies, "sah_csrf"));
        c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        String name = displayName(u).replace("\"", "");
        String mime = getContentResolver().getType(u);
        try (DataOutputStream out = new DataOutputStream(c.getOutputStream()); InputStream in = getContentResolver().openInputStream(u)) {
            out.writeBytes("--" + boundary + "\r\nContent-Disposition: form-data; name=\"folder\"\r\n\r\nAndroid\r\n");
            out.writeBytes("--" + boundary + "\r\nContent-Disposition: form-data; name=\"add_to_knowledge\"\r\n\r\nfalse\r\n");
            out.writeBytes("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"");
            out.write(name.getBytes("UTF-8"));
            out.writeBytes("\"\r\nContent-Type: " + (mime != null ? mime : "application/octet-stream") + "\r\n\r\n");
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            out.writeBytes("\r\n--" + boundary + "--\r\n");
        }
        int code = c.getResponseCode();
        InputStream rs = code < 400 ? c.getInputStream() : c.getErrorStream();
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        byte[] b = new byte[8192];
        int k;
        while (rs != null && (k = rs.read(b)) > 0) bo.write(b, 0, k);
        String body = bo.toString("UTF-8");
        if (code >= 400) throw new Exception(name + ": HTTP " + code);
        int i = body.indexOf("\"id\":\"");
        return body.substring(i + 6, body.indexOf('"', i + 6));
    }
}
