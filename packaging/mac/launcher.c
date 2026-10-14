/* VoiceInput.app's executable: starts VoiceInput's Python as a child process and waits for it.

   macOS grants the microphone, Accessibility and Input Monitoring to the app a process belongs to: a child
   process counts as its parent app's. So the permissions belong to this small launcher, which never changes when
   VoiceInput is updated (its code lives outside the bundle, in ~/Library/Application Support/VoiceInput), and the
   ad-hoc signature, made the same way from the same bytes, stays the same: the user allows them once.

   Built universal (x86_64 + arm64) by CI (.github/workflows/mac.yml) and committed as packaging/mac/VoiceInput. */
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

static void alert(const char *message) {
    char script[1024];
    snprintf(script, sizeof script, "display alert \"VoiceInput\" message \"%s\"", message);
    char *args[] = {"/usr/bin/osascript", "-e", script, NULL};
    pid_t pid;
    if (posix_spawn(&pid, args[0], NULL, NULL, args, environ) == 0) waitpid(pid, NULL, 0);
}

int main(void) {
    const char *home = getenv("HOME");
    const char *root_env = getenv("VOICEINPUT_ROOT");
    char root[PATH_MAX], python[PATH_MAX], script[PATH_MAX], exe[PATH_MAX], app[PATH_MAX];
    if (root_env && *root_env)
        snprintf(root, sizeof root, "%s", root_env);
    else
        snprintf(root, sizeof root, "%s/Library/Application Support/VoiceInput", home ? home : "");
    snprintf(python, sizeof python, "%s/venv/bin/python3", root);
    snprintf(script, sizeof script, "%s/source/run.pyw", root);

    /* the bundle's path, for launch at login (mac/autostart.py) */
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) == 0 && realpath(exe, app)) {
        for (int i = 0; i < 3; i++) {   /* .../VoiceInput.app/Contents/MacOS/VoiceInput */
            char *slash = strrchr(app, '/');
            if (slash) *slash = '\0';
        }
        setenv("VOICEINPUT_APP", app, 1);
    }

    if (access(python, X_OK) != 0 || access(script, R_OK) != 0) {
        alert("VoiceInput is not fully installed. Run the install command again.");
        return 1;
    }
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);
    char *args[] = {python, script, NULL};
    if (posix_spawn(&child, python, NULL, NULL, args, environ) != 0) {
        alert("VoiceInput could not start Python. Run the install command again.");
        return 1;
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        if (errno != EINTR) return 1;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
