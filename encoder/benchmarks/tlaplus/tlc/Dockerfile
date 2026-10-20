FROM eclipse-temurin:21-jre
# TLA+ tools v1.8.0 (rev 932d35e, 2026-10-15) and CommunityModules 202610040242. v1.8.0 is a rolling
# prerelease: if upstream replaces the jar, the checksum fails and both pins need updating together.
ADD --checksum=sha256:15d103b7e5617780bcc904c0af12f1ad242e2b345c0301136c5be4588f17edac \
    https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar /opt/tla/tla2tools.jar
ADD --checksum=sha256:dddb19c3d7596913d92c43073b60d1408ac8f6d86716af4d19dd6861a95bfb7d \
    https://github.com/tlaplus/CommunityModules/releases/download/202610040242/CommunityModules-deps-202610040242.jar \
    /opt/tla/CommunityModules-deps.jar
# TLAPS proof-support modules (TLAPS.tla, ...), which many specs EXTEND; TLC ignores the proofs.
ADD https://github.com/tlaplus/tlapm.git#85b548aab8c0823aa41c31fe538ba011fc409ee4:library /opt/tla/tlapm-library
RUN chmod -R a+rX /opt/tla
ENV CLASSPATH=/opt/tla/tla2tools.jar:/opt/tla/CommunityModules-deps.jar
ENV JAVA_TOOL_OPTIONS="-DTLA-Library=/opt/tla/tlapm-library -XX:+UseParallelGC"
WORKDIR /work
