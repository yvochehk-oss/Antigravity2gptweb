# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# If your project uses WebView with JS, uncomment the following
# and specify the fully qualified class name to the JavaScript interface
# class:
#-keepclassmembers class fqcn.of.javascript.interface.for.webview {
#   public *;
#}

# Keep enough metadata to symbolicate release crashes without preserving local
# source file paths in the APK.
-keepattributes SourceFile,LineNumberTable,*Annotation*

# If you keep the line number information, uncomment this to
# hide the original source file name.
-renamesourcefileattribute SourceFile

# Preserve Capacitor bridge classes so the release build can still talk to the WebView.
-keep class com.getcapacitor.** { *; }
