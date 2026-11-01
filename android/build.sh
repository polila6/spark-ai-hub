#!/usr/bin/env bash
# Build the Spark AI Hub Android APK on the DGX Spark (aarch64) without Gradle:
#   aapt2 (Ubuntu arm64 package) → javac → d8 (Google build-tools jar) → zipalign → apksigner.
# Usage: ANDROID_TOOLS=<dir with usr/bin/aapt2 + libs> ANDROID_SDK=<dir with platform/ and bt/> ./build.sh
# Output: /srv/spark-ai-hub/apps/spark-ai-hub.apk (served by the hub at /api/v1/app/android).
set -euo pipefail
cd "$(dirname "$0")"
: "${ANDROID_TOOLS:?set ANDROID_TOOLS}"; : "${ANDROID_SDK:?set ANDROID_SDK}"
export LD_LIBRARY_PATH="$ANDROID_TOOLS/usr/lib/aarch64-linux-gnu/android:$ANDROID_TOOLS/usr/lib/aarch64-linux-gnu:$ANDROID_TOOLS/usr/lib"
AAPT2="$ANDROID_TOOLS/usr/bin/aapt2"; ZIPALIGN="$ANDROID_TOOLS/usr/bin/zipalign"
# android-34 resources: the Ubuntu aapt2 (Android 14) cannot parse the android-35 sparse resource table
JAR="$ANDROID_SDK/platform/android-34/android.jar"; BT="$ANDROID_SDK/bt/android-15/lib"
SECRETS=${SAH_SECRETS:-/srv/spark-ai-hub/config/secrets}; OUT=${SAH_APPS:-/srv/spark-ai-hub/apps}
B=build; rm -rf "$B"; mkdir -p "$B/flat" "$B/gen" "$B/classes" "$B/src" "$OUT"
# The app opens the hub's address by default (users can change it in the app): SAH_PUBLIC_BASE_URL or ../.env.
URL="${SAH_PUBLIC_BASE_URL:-$(sed -n 's/^SAH_PUBLIC_BASE_URL=\([^ #]*\).*/\1/p' ../.env 2>/dev/null)}"
: "${URL:?set SAH_PUBLIC_BASE_URL (e.g. https://your-spark.your-tailnet.ts.net)}"
cp -r src/. "$B/src/"; sed -i "s#__SAH_PUBLIC_BASE_URL__#${URL}#" "$B/src/es/upv/idf/sparkhub/MainActivity.java"
"$AAPT2" compile --dir res -o "$B/flat/res.zip"
"$AAPT2" link -I "$JAR" --manifest AndroidManifest.xml -o "$B/base.apk" --java "$B/gen" "$B/flat/res.zip" --min-sdk-version 26 --target-sdk-version 34
javac --release 11 -nowarn -classpath "$JAR" -d "$B/classes" $(find "$B/src" "$B/gen" -name '*.java')
java -cp "$BT/d8.jar" com.android.tools.r8.D8 --release --min-api 26 --lib "$JAR" --output "$B" $(find "$B/classes" -name '*.class')
cp "$B/base.apk" "$B/unaligned.apk"
(cd "$B" && zip -q -j unaligned.apk classes.dex)
"$ZIPALIGN" -f -p 4 "$B/unaligned.apk" "$B/aligned.apk"
KS="$SECRETS/android-release.jks"; KP="$SECRETS/android-keystore-password"
if [ ! -f "$KS" ]; then
  umask 077; head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$KP"
  keytool -genkeypair -keystore "$KS" -storepass:file "$KP" -keypass:file "$KP" -alias sparkhub -keyalg RSA -keysize 3072 \
    -validity 10000 -dname "CN=Spark AI Hub" >/dev/null
fi
java -jar "$BT/apksigner.jar" sign --ks "$KS" --ks-pass "file:$KP" --ks-key-alias sparkhub \
  --min-sdk-version 26 --out "$OUT/spark-ai-hub.apk" "$B/aligned.apk"
java -jar "$BT/apksigner.jar" verify --print-certs "$OUT/spark-ai-hub.apk" | head -3
ls -l "$OUT/spark-ai-hub.apk"
