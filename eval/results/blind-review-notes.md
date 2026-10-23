# Blind review: reasons for the grades below 2 (written before the key was opened)

Each model was given the headings outline plus the first 3,000 characters; claims about later sections were
checked against the headings. Grades are [faithful, specific].

- authentication-mechanisms D [0,2]: "password-protected keys that expose secrets" — the page says the keys
  *cannot* be password-protected. A [2,1]: correct but says only "various built-in methods".
- cluster-administration B [1,2]: "production-ready environments" is not in the page. A, D, E [2,1]: correct
  lists of topics any cluster page could have.
- autoscaling C [1,2]: says HPA and VPA scale "based on cluster size or schedules"; those are separate methods.
- api-server-bypass-risks C [1,2]: calls the bypasses "security vulnerabilities in the API server"; the page
  is about paths around the API server. E [2,1]: reply was raw, cut-off JSON with repeated topics — the
  app cannot parse it, so a reader would see no summary.
- cloud-native-security E [1,2]: "how Kubernetes secures the development ... phases" overstates the page.
- admission-webhooks-good-practices A [2,1]: raw, cut-off JSON (unparsable), content otherwise correct.
- authorization E [0,1]: "deny decisions made after authorization modules complete" and requests "must pass
  through admission controllers" — admission runs only after an allow; the authorizers make the deny.
  D [2,1]: restates the title.
- architecture B [0,2]: lists cloud-controller-manager among the worker-node components; it is a control
  plane component. C, E [2,1]: generic.
