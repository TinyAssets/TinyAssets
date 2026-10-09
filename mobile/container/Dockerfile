# Android build toolchain for the TinyAssets Capacitor shell.
#
# Targets API 36 because Google Play has required it of NEW apps since
# 2026-08-31 (developer.android.com/google/play/requirements/target-sdk).
# Capacitor 8 defaults to compileSdk/targetSdk 36, needs node >= 22, and
# compiles its Java at source/target 21 -- JDK 17 fails with "invalid source release: 21".
FROM mirror.gcr.io/library/ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV ANDROID_HOME=/opt/android-sdk

RUN apt-get update && apt-get install -y --no-install-recommends \
      openjdk-21-jdk-headless \
      curl ca-certificates unzip zip git python3 \
    && rm -rf /var/lib/apt/lists/*

# Node 22 — @capacitor/cli@8 declares engines.node >= 22.0.0
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Android command-line tools
RUN mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools" \
    && curl -fsSL -o /tmp/cmdline.zip \
       https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
    && unzip -q /tmp/cmdline.zip -d /tmp/cmdline \
    && mv /tmp/cmdline/cmdline-tools "$ANDROID_SDK_ROOT/cmdline-tools/latest" \
    && rm -rf /tmp/cmdline.zip /tmp/cmdline

ENV PATH="${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${PATH}"

RUN yes | sdkmanager --licenses > /dev/null \
    && sdkmanager --install "platform-tools" "platforms;android-36" "build-tools;36.0.0" > /dev/null \
    && echo "sdk ready"

WORKDIR /work
