(set-info :smt-lib-version 2.6)
(set-logic QF_S)
(set-info :license "https://creativecommons.org/licenses/by/4.0/")
(set-info :category "industrial")
(set-info :status sat)

(declare-const X String)
(assert (str.in_re X (str.to_re "www.maxifiles.comServidor.\u{13}\u{a}")))
(check-sat)

(exit)
