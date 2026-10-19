/* Runs pam_authenticate for one service from a private PAM config
 * directory, so the module can be tested without touching /etc/pam.d.
 * Usage: harness <confdir> <service> <user>
 * Prints the PAM result name and every conversation message, answers
 * password prompts with "wrong-password". */
#include <security/pam_appl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int conversation(int count, const struct pam_message **messages,
                        struct pam_response **responses, void *data) {
	(void)data;
	*responses = calloc((size_t)count, sizeof(struct pam_response));
	for (int i = 0; i < count; i++) {
		printf("message %d: %s\n", messages[i]->msg_style, messages[i]->msg);
		if (messages[i]->msg_style == PAM_PROMPT_ECHO_OFF) {
			(*responses)[i].resp = strdup("wrong-password");
		}
	}
	fflush(stdout);
	return PAM_SUCCESS;
}

int main(int argc, char **argv) {
	if (argc != 4) {
		fprintf(stderr, "usage: %s <confdir> <service> <user>\n", argv[0]);
		return 2;
	}
	struct pam_conv conv = {conversation, NULL};
	pam_handle_t *handle = NULL;
	int result = pam_start_confdir(argv[2], argv[3], &conv, argv[1], &handle);
	if (result != PAM_SUCCESS) {
		printf("start: %s\n", pam_strerror(handle, result));
		return 1;
	}
	result = pam_authenticate(handle, 0);
	printf("result: %s\n", pam_strerror(handle, result));
	pam_end(handle, result);
	return result == PAM_SUCCESS ? 0 : 1;
}
