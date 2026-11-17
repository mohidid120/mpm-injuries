report_time <- local({
 cache <- new.env(parent=emptyenv())
 function(f) {
  if(exists(f,cache,inherits=FALSE)) return(get(f,cache,inherits=FALSE))
  header <- pdftools::pdf_text(f)[1]
  stamp <- regmatches(header,regexpr("Injury Report: *[0-9/]+ +[0-9:]+ +[AP]M",header))
  t <- as.POSIXct(sub("Injury Report: *","",stamp),format="%m/%d/%y %I:%M %p",tz="America/New_York")
  if(length(t)!=1L || is.na(t)) stop("Missing printed issue timestamp in ",f)
  assign(f,t,cache);t
 }
})

# Resolve legacy hourly filenames and read the fixture times printed in each PDF.
report_pdf <- local({
 cache <- new.env(parent=emptyenv())
 function(snapshot) {
  f <- file.path('data/injury_reports/pdf',paste0(snapshot,'.pdf'))
  if(file.exists(f)) return(f)
  day <- substr(snapshot,1,10)
  if(!exists(day,cache,inherits=FALSE)) {
   fs <- Sys.glob(file.path('data/injury_reports/pdf',paste0(day,'_*.pdf')))
   stamps <- vapply(fs,function(f)tryCatch(format(report_time(f),'%Y-%m-%d_%I_%M%p',tz='America/New_York'),error=function(e)NA_character_),'')
   assign(day,setNames(fs,stamps),cache)
  }
  fs <- get(day,cache,inherits=FALSE); unname(fs[match(snapshot,names(fs))])
 }
})
report_fixtures <- local({
 cache <- new.env(parent=emptyenv())
 function(f) {
  if(exists(f,cache,inherits=FALSE)) return(get(f,cache,inherits=FALSE))
  lines <- unlist(strsplit(pdftools::pdf_text(f),'\n'))
  day <- NA_character_; tip <- NA_character_; rows <- list()
  for(line in lines) {
   m <- regmatches(line,regexpr('^[[:space:]]*[0-9]{2}/[0-9]{2}/[0-9]{4}',line))
   if(length(m)) day <- trimws(m)
   m <- regmatches(line,regexpr('[0-9]{1,2}:[0-9]{2} *\\(ET\\)',line))
   if(length(m)) {
    hh <- as.integer(sub(':.*','',m)); mm <- sub('^[0-9]+:([0-9]+).*','\\1',m)
    tip <- sprintf('%02d:%s',if(hh<12L) hh+12L else hh,mm)
   }
   m <- regmatches(line,regexpr('[A-Z]{2,3}@[A-Z]{2,3}',line))
   if(length(m)) rows[[length(rows)+1L]] <- data.table::data.table(game_date=day,matchup=m,tip_et=tip)
  }
  x <- unique(data.table::rbindlist(rows));assign(f,x,cache);x
 }
})

# Reject publications later than the fixture's real tip-minus-15 cutoff.
verified_pregame <- function(rep) {
 rep <- data.table::copy(rep)
 if("game_date" %in% names(rep)) rep <- rep[format(as.Date(game_date,"%m/%d/%Y"))==report_date]
 for(stamp in unique(rep$snapshot)) {
  ix <- which(rep$snapshot==stamp); f <- report_pdf(stamp)
  if(length(f)==1L && !is.na(f) && file.exists(f)) {
   rep$snapshot[ix] <- format(report_time(f),'%Y-%m-%d_%I_%M%p',tz='America/New_York')
   if(all(c('game_date','matchup') %in% names(rep))) {
    fixtures <- report_fixtures(f)
    j <- match(paste(rep$game_date[ix],rep$matchup[ix]),paste(fixtures$game_date,fixtures$matchup))
    rep$tip_et[ix] <- fixtures$tip_et[j]
   }
  } else if('game_date' %in% names(rep) || grepl('_[0-9]{2}[AP]M$',stamp)) rep$snapshot[ix] <- NA_character_
 }
 issue <- as.POSIXct(rep$snapshot,format='%Y-%m-%d_%I_%M%p',tz='America/New_York')
 cutoff <- as.POSIXct(paste(rep$report_date,rep$tip_et),tz='America/New_York')-900
 unique(rep[!is.na(issue) & !is.na(cutoff) & issue<=cutoff])
}
