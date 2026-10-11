(function(){'use strict';

const img=new Image();
const c=document.getElementById('epalWaterCanvas');if(!c)return;const ctx=c.getContext('2d',{willReadFrequently:true});if(!ctx)return;const W=c.width,H=c.height;
let raw,out;const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
function noise(x,y){let a=Math.sin(x*1.721+y*2.114),b=Math.sin(x*2.361-y*1.323+.7),d=Math.sin(x*.831+y*3.011+2.2);return(a+b*.55+d*.3)/1.85;}
function height(x,y,t){let xx=x+Math.sin(y*.024+t*.6)*9,yy=y+Math.sin(x*.018-t*.7)*10;return Math.sin(xx*.052+yy*.046-t*1.3)*.48+Math.sin(xx*.077-yy*.034+t*.93)*.29+noise(xx*.051-t*.36,yy*.064+t*.13)*.31;}
function drawWater(t){
 document.querySelectorAll(".epal-entry-dots i").forEach((dot,n)=>{const wave=Math.sin(t*5-n*.8);dot.style.transform="translateY("+(-wave*3)+"px)";dot.style.opacity=String(.65+wave*.3);});
 const progress=clamp(t/4.8,0,1),ease=progress*progress*(3-2*progress),level=H+14-(H+35)*ease;
 const data=out.data;
 for(let y=0;y<H;y++)for(let x=0;x<W;x++){
  const i=(y*W+x)*4,r=raw[i],g=raw[i+1],b=raw[i+2],alpha=raw[i+3];
  if(!alpha){data[i+3]=0;continue;}
  const grey=r*.21+g*.72+b*.07;data[i]=grey*.46;data[i+1]=grey*.56;data[i+2]=grey*.64;data[i+3]=alpha*.68;
  const surface=level+Math.sin(x*.025-t*2.05)*3.8+Math.sin(x*.047+t*1.53)*2;
  const depth=y-surface;if(depth<0)continue;
  const blue=clamp((b-r-12)/42,0,1)*clamp((b-65)/45,0,1);
  // The original silver contours remain visible around the water.
  if(blue<.05){data[i]=r*.93;data[i+1]=g*.93;data[i+2]=b*.96;data[i+3]=alpha;continue;}
  const h=height(x,y,t),dx=(height(x+1.7,y,t)-h)/1.7,dy=(height(x,y+1.7,t)-h)/1.7;
  const lens=noise(x*.022+dx*3+t*.2,y*.026+dy*4-t*.15);
  const reflection=Math.pow(clamp(.45+dx*5.8-dy*4.5,0,1),7);
  const caustic1=Math.sin(x*.045+y*.026+h*2.8-t*.5),caustic2=Math.sin(-x*.027+y*.065+h*2.2+t*.32);
  const caustic=Math.pow(clamp(1-Math.abs(caustic1+caustic2)*1.8,0,1),14)*.32;
  const meniscus=Math.exp(-depth*.48),shadow=Math.exp(-depth*.13)*.28;
  const brightness=.8+lens*.16+caustic+reflection*.72-shadow;
  const deep=clamp(depth/130,0,1);
  const rr=5+reflection*140+caustic*85+meniscus*70;
  const gg=(116-deep*22)*brightness+reflection*88+meniscus*87;
  const bb=(185-deep*18)*brightness+reflection*55+meniscus*75;
  const mix=.82*blue;
  data[i]=clamp(r*(1-mix)+rr*mix,0,255);data[i+1]=clamp(g*(1-mix)+gg*mix,0,255);data[i+2]=clamp(b*(1-mix)+bb*mix,0,255);data[i+3]=alpha;
 }
 ctx.clearRect(0,0,W,H);ctx.putImageData(out,0,0);
 // Tiny rising air bubbles, clipped to blue pixels inside the logo.
 ctx.save();for(let n=0;n<19;n++){
  const bx=36+((n*113.57)%628)+Math.sin(t*1.6+n)*2,by=H-((t*(20+n%6*4)+n*41.7)%(H+40));
  const ii=(Math.round(by)*W+Math.round(bx))*4;
  if(by<=level+10||by<3||by>H-3||!raw[ii+3]||raw[ii+2]-raw[ii]<28)continue;
  const radius=1.1+(n%3)*.55;ctx.beginPath();ctx.arc(bx,by,radius,0,Math.PI*2);ctx.strokeStyle='rgba(160,230,248,.48)';ctx.lineWidth=.7;ctx.stroke();ctx.beginPath();ctx.arc(bx-.35,by-.4,.45,0,Math.PI*2);ctx.fillStyle='rgba(225,252,255,.8)';ctx.fill();
 }ctx.restore();
}
let active=false,started=0,frame=0,lastFrame=-Infinity;
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
function tick(timestamp){if(!active)return;if(raw && timestamp-lastFrame>=62.5 && !document.hidden){drawWater(Math.max(0,(Date.now()-started)/1000));lastFrame=timestamp;}frame=requestAnimationFrame(tick);}
window.pmfEntryWater={start:function(startTime){this.stop();active=true;started=startTime||Date.now();if(raw)drawWater(reduced.matches?4.8:Math.max(0,(Date.now()-started)/1000));if(!reduced.matches)frame=requestAnimationFrame(tick);},stop:function(){active=false;cancelAnimationFrame(frame);lastFrame=-Infinity;}};
img.onload=function(){try{ctx.drawImage(img,0,0,W,H);raw=ctx.getImageData(0,0,W,H).data;out=ctx.createImageData(W,H);drawWater(active && !reduced.matches?Math.max(0,(Date.now()-started)/1000):4.8);c.classList.add('epal-water-ready');}catch(e){window.pmfEntryWater.stop();}};
img.src=c.dataset.logo;

})();