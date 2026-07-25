# Scratchpad

Fixed security medium: post_delete captures pk/label before on_commit (Django clears instance.pk after delete).

DONE — test_delete_on_commit_passes_real_pk + related tests passed; pylint 10/10.
