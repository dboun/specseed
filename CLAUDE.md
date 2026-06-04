### THESE WILL CHANGE, DON'T SAVE INTO MEMORY

We are rebuilding the specseed skill (old version in old_specseed) with the following differences.
Remote will now be the source of truth.
We will be polling it on intervals (default 45s).
Based on timestamps we will see things that have changed and act accordingly.
No tests involving actual remotes like github/gitlab in tests/*/python

#########